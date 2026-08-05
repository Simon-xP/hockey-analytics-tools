"""Fit the per-game fantasy-points variance model used by the weekly optimizer.

`src/optimize/week/variance.py` turns a projection into a distribution. Under a
`DELTA P(win)` objective that curve *is* the risk behaviour of the whole
optimizer, so it has to be measured rather than guessed.

What we measure is **predictive** variance, not the raw variance of game FPTS:
how far actual outcomes land from what our own model projected. That folds
true game-to-game noise together with our projection error, which is exactly
the uncertainty `P(win)` should reflect.

Two phases, so the fit can be re-run without re-harvesting:

    # ~30 min: score player-games with the live forecast path, join to actuals
    python -m scripts.fit_variance_model harvest --season 20252026 --dates 45

    # seconds: fit sigma(mu), print coefficients and the calibration check
    python -m scripts.fit_variance_model fit

Residuals land in `data/variance_residuals.csv`.

Caveat worth knowing: the shipped forecasting models were trained on seasons
that include the ones we can measure on, so feature extraction is `as_of`-gated
but the model parameters have seen the outcomes. That biases the fitted sigma
*downwards*. The calibration check at the end of `fit` is the guard: if sigma
is understated, interval coverage comes in below the target band and the fit
should not be shipped.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
from sqlalchemy import text

from src.core.db import get_session
from src.core.scoring import SKATER_WEIGHTS
from src.predict.forecasting.constants import SITUATION_CONFIGS
from src.predict.forecasting.empirical_bayes import EmpiricalBayesPredictor
from src.predict.forecasting.forecast import predict_situation_rates
from src.predict.forecasting.model import SituationModel
from src.predict.forecasting.pim import PIM_PER_PENALTY, project_pim_per_game
from src.predict.forecasting.projections import compute_actual_fpts, project_per_game
from src.predict.forecasting.toi_model import TOIPredictor

RESIDUALS_PATH = Path("data/variance_residuals.csv")
MODEL_DIR = Path("models/forecasting_v2")

# Minimum prior games in the season before we will project a player. Mirrors
# the gate in `src/predict/forecasting/evaluation.py`.
MIN_PRIOR_GAMES = 10

# Minimum 5v5 seconds in the game being measured. `evaluation.py` uses 300,
# which quietly excludes fourth-liners and third-pair D — i.e. the entire
# streaming pool, which is where the low end of sigma(mu) has to be right.
DEFAULT_MIN_TOI_SECONDS = 60

STAT_COLUMNS = ["goals", "assists", "shots", "hits", "blocks", "pim"]

FIELDNAMES = (
    ["nhl_id", "game_date", "position", "team_id", "projected_fpts", "actual_fpts",
     "projected_toi", "actual_toi"]
    + [f"projected_{s}" for s in STAT_COLUMNS]
    + [f"actual_{s}" for s in STAT_COLUMNS]
)


# ---------------------------------------------------------------------------
# Phase 1: harvest
# ---------------------------------------------------------------------------


def _load_deps():
    models = {}
    for situation in SITUATION_CONFIGS:
        path = MODEL_DIR / f"{situation}_model.pkl"
        if path.exists():
            models[situation] = SituationModel.load(path)
    if not models:
        raise RuntimeError(f"No trained forecasting models in {MODEL_DIR}")
    return (
        models,
        TOIPredictor(),
        EmpiricalBayesPredictor("pp", ["goals", "assists", "shots"]),
        EmpiricalBayesPredictor("pk", ["goals", "assists"]),
        EmpiricalBayesPredictor("5v5", ["goals", "assists"]),
    )


def _season_game_id_range(season: str) -> tuple[int, int]:
    year = int(season[:4])
    return year * 1_000_000, (year + 1) * 1_000_000


def _sample_dates(session, season: str, n_dates: int | None) -> list[date]:
    start_gid, end_gid = _season_game_id_range(season)
    rows = session.execute(
        text(
            """
            SELECT DISTINCT g.date
            FROM game_advanced_stats gas
            JOIN games g ON gas.game_id = g.game_id
            WHERE gas.situation = '5v5'
                  AND gas.game_id >= :start AND gas.game_id < :end
                  AND gas.toi_seconds > 0
            ORDER BY g.date
            """
        ),
        {"start": start_gid, "end": end_gid},
    ).fetchall()
    dates = [r[0] for r in rows]

    # Skip the opening fortnight: nobody clears MIN_PRIOR_GAMES yet.
    dates = dates[14:]
    if n_dates is None or n_dates >= len(dates):
        return dates

    # Even spread across the season rather than a random clump, so early- and
    # late-season noise regimes are both represented.
    step = len(dates) / n_dates
    return [dates[int(i * step)] for i in range(n_dates)]


def harvest(
    season: str,
    n_dates: int | None,
    out_path: Path,
    min_toi_seconds: int = DEFAULT_MIN_TOI_SECONDS,
) -> None:
    models, toi_predictor, eb_pp, eb_pk, eb_5v5 = _load_deps()
    start_gid, end_gid = _season_game_id_range(season)
    start_year = int(season[:4])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0

    with get_session() as session, out_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDNAMES)

        dates = _sample_dates(session, season, n_dates)
        print(f"Harvesting {len(dates)} game dates from {season}", flush=True)

        for idx, game_date in enumerate(dates, start=1):
            player_rows = session.execute(
                text(
                    """
                    SELECT DISTINCT gas.player_id, gas.team_id, gas.opponent_team_id,
                           g.home_team_id, gas.game_id, p.position
                    FROM game_advanced_stats gas
                    JOIN games g ON gas.game_id = g.game_id
                    LEFT JOIN players p ON p.nhl_id = gas.player_id
                    WHERE gas.situation = '5v5'
                          AND g.date = :gd
                          AND gas.game_id >= :start AND gas.game_id < :end
                          AND gas.toi_seconds >= :min_toi
                          AND (p.position IS NULL OR p.position <> 'G')
                    """
                ),
                {"gd": game_date, "start": start_gid, "end": end_gid,
                 "min_toi": min_toi_seconds},
            ).fetchall()

            for player_id, team_id, opp_team_id, home_team_id, game_id, position in player_rows:
                prior_games = session.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM game_advanced_stats gas
                        JOIN games g ON gas.game_id = g.game_id
                        WHERE gas.player_id = :pid AND gas.situation = '5v5'
                              AND g.date < :gd AND gas.toi_seconds > 0
                              AND gas.game_id >= :start
                        """
                    ),
                    {"pid": player_id, "gd": game_date, "start": start_gid},
                ).scalar()
                if prior_games < MIN_PRIOR_GAMES:
                    continue

                actual_by_sit, actual_toi_by_sit = _load_actuals(session, player_id, game_id)
                if not actual_toi_by_sit:
                    continue

                rates, toi = predict_situation_rates(
                    session, player_id, game_date,
                    team_id, opp_team_id, home_team_id,
                    position or "C", start_year,
                    models, toi_predictor, eb_pp, eb_pk, eb_5v5,
                )
                projected = project_per_game(rates, toi)
                actual = compute_actual_fpts(actual_by_sit, actual_toi_by_sit)

                # PIM lives outside the situation model, so add it on both
                # sides to measure the quantity `forecast_player` now returns
                # (pre-calibration; the calibration is what we are fitting).
                projected["pim"] = project_pim_per_game(session, player_id, as_of=game_date)
                actual_pim = _actual_pim(session, player_id, game_id)
                projected_fpts = projected["fpts"] + projected["pim"] * SKATER_WEIGHTS["pim"]
                actual_fpts = actual + actual_pim * SKATER_WEIGHTS["pim"]
                actuals = {
                    s: sum(sit.get(s, 0) for sit in actual_by_sit.values())
                    for s in STAT_COLUMNS
                    if s != "pim"
                }
                actuals["pim"] = round(actual_pim, 4)

                writer.writerow(
                    [
                        player_id, game_date.isoformat(), position or "C", team_id,
                        round(projected_fpts, 4), round(actual_fpts, 4),
                        round(sum(toi.values()) / 60.0, 3),
                        round(sum(actual_toi_by_sit.values()) / 60.0, 3),
                    ]
                    + [round(projected.get(s, 0.0), 4) for s in STAT_COLUMNS]
                    + [actuals[s] for s in STAT_COLUMNS]
                )
                written += 1

            fh.flush()
            print(f"  [{idx}/{len(dates)}] {game_date}: {written} rows total", flush=True)

    print(f"Wrote {written} residuals to {out_path}")


def _actual_pim(session, player_id: int, game_id: int) -> float:
    """Observed penalty minutes for one player-game.

    An estimate, not ground truth: `game_advanced_stats` stores a penalty
    *count*, and Natural Stat Trick's true `pim_per_60` only covers 2023-24
    and 2024-25. `PIM_PER_PENALTY` is fitted against NST over the overlap, so
    the mean is right; the game-to-game lumpiness of majors is smoothed out,
    which slightly understates PIM's variance contribution. PIM is 0.14 FPTS
    per game on average, so the effect on total sigma is small.
    """
    penalties = session.execute(
        text(
            """
            SELECT COALESCE(SUM(penalties), 0)
            FROM game_advanced_stats
            WHERE player_id = :pid AND game_id = :gid AND situation = 'all'
            """
        ),
        {"pid": player_id, "gid": game_id},
    ).scalar()
    return float(penalties or 0) * PIM_PER_PENALTY


def _load_actuals(session, player_id: int, game_id: int):
    actual_by_sit: dict[str, dict[str, int]] = {}
    actual_toi_by_sit: dict[str, float] = {}
    for sit in SITUATION_CONFIGS:
        if sit == "other":
            sit_filter = "gas.situation IN ('4v4', '3v3', 'other')"
        else:
            sit_filter = f"gas.situation = '{sit}'"
        row = session.execute(
            text(
                f"""
                SELECT SUM(gas.goals), SUM(gas.assists), SUM(gas.shots),
                       SUM(gas.hits), SUM(gas.blocks), SUM(gas.toi_seconds)
                FROM game_advanced_stats gas
                WHERE gas.player_id = :pid AND gas.game_id = :gid AND {sit_filter}
                """
            ),
            {"pid": player_id, "gid": game_id},
        ).fetchone()
        if row and row[5] and row[5] > 0:
            actual_by_sit[sit] = {
                "goals": row[0] or 0,
                "assists": row[1] or 0,
                "shots": row[2] or 0,
                "hits": row[3] or 0,
                "blocks": row[4] or 0,
            }
            actual_toi_by_sit[sit] = row[5]
    return actual_by_sit, actual_toi_by_sit


# ---------------------------------------------------------------------------
# Phase 2: fit
# ---------------------------------------------------------------------------


def _read_residuals(path: Path):
    cols = defaultdict(list)
    with path.open() as fh:
        for row in csv.DictReader(fh):
            for key, value in row.items():
                cols[key].append(value)
    out = {}
    for key, values in cols.items():
        if key in ("game_date", "position"):
            out[key] = np.array(values)
        else:
            out[key] = np.array([float(v) for v in values])
    return out


def _bucket_sigmas(mu: np.ndarray, resid: np.ndarray, n_buckets: int = 20):
    """Equal-count buckets by projected FPTS -> (bucket_mean_mu, bucket_sigma, n)."""
    order = np.argsort(mu)
    mu, resid = mu[order], resid[order]
    splits = np.array_split(np.arange(len(mu)), n_buckets)
    out = []
    for idx in splits:
        if len(idx) < 30:
            continue
        out.append((float(mu[idx].mean()), float(resid[idx].std(ddof=1)), len(idx)))
    return out


def _fit_forms(buckets):
    """Fit constant-CV, affine, and power forms to (mu, sigma) buckets."""
    b_mu = np.array([b[0] for b in buckets])
    b_sig = np.array([b[1] for b in buckets])
    w = np.array([b[2] for b in buckets], dtype=float)

    fits = {}

    # sigma = k * mu, weighted least squares through the origin
    k = float((w * b_mu * b_sig).sum() / (w * b_mu * b_mu).sum())
    fits["constant_cv"] = {"params": {"k": k}, "predict": lambda m, k=k: k * m}

    # sigma = a + b * mu
    coef = np.polyfit(b_mu, b_sig, 1, w=np.sqrt(w))
    b_, a_ = float(coef[0]), float(coef[1])
    fits["affine"] = {"params": {"a": a_, "b": b_}, "predict": lambda m, a=a_, b=b_: a + b * m}

    # sigma = c * mu ** p  (log-log, positive mu only)
    pos = b_mu > 0.05
    logfit = np.polyfit(np.log(b_mu[pos]), np.log(b_sig[pos]), 1, w=np.sqrt(w[pos]))
    p_, c_ = float(logfit[0]), float(np.exp(logfit[1]))
    fits["power"] = {
        "params": {"c": c_, "p": p_},
        "predict": lambda m, c=c_, p=p_: c * np.maximum(m, 0.0) ** p,
    }

    for name, fit in fits.items():
        pred = np.asarray(fit["predict"](b_mu), dtype=float)
        fit["rmse"] = float(np.sqrt((w * (pred - b_sig) ** 2).sum() / w.sum()))
    return fits


def _calibration(mu, resid, sigma_fn, nhl_id, game_date, rng, n_trials=4000, bundle=25):
    """Coverage of an 80% interval on synthetic weekly bundles.

    A fantasy week is ~25 player-games. Draw that many at random, sum the
    projections and the actuals, and check how often the actual total lands
    inside the model's 80% interval. Independence is assumed, matching
    `team_sigma`.
    """
    n = len(mu)
    z = 1.2815515655446004  # 90th percentile of the standard normal
    inside = 0
    for _ in range(n_trials):
        idx = rng.integers(0, n, size=bundle)
        total_mu = mu[idx].sum()
        total_actual = total_mu + resid[idx].sum()
        total_sigma = float(np.sqrt((np.asarray(sigma_fn(mu[idx]), dtype=float) ** 2).sum()))
        if total_sigma <= 0:
            continue
        if abs(total_actual - total_mu) <= z * total_sigma:
            inside += 1
    return inside / n_trials


def _linemate_correlation(mu, resid, nhl_id, game_date, team_id, min_pairs=20):
    """Average residual correlation between same-team skaters on the same date."""
    by_team_date = defaultdict(list)
    for i in range(len(mu)):
        by_team_date[(team_id[i], game_date[i])].append(i)

    pair_resids = defaultdict(list)
    for idxs in by_team_date.values():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                key = (min(nhl_id[i], nhl_id[j]), max(nhl_id[i], nhl_id[j]))
                pair_resids[key].append((resid[i], resid[j]))

    corrs = []
    for pairs in pair_resids.values():
        if len(pairs) < min_pairs:
            continue
        arr = np.array(pairs)
        if arr[:, 0].std() == 0 or arr[:, 1].std() == 0:
            continue
        corrs.append(float(np.corrcoef(arr[:, 0], arr[:, 1])[0, 1]))

    if not corrs:
        return None, 0
    return float(np.mean(corrs)), len(corrs)


def _report_bias(data) -> None:
    """Where the projection's systematic error comes from.

    Not a variance question, but the calibration check cannot be read without
    it: a biased mean pushes weekly totals outside the interval no matter how
    well sigma is fitted.
    """
    mu, actual = data["projected_fpts"], data["actual_fpts"]
    print("Bias decomposition (mean projected vs mean actual)")
    print(f"  {'quantity':<12}{'projected':>11}{'actual':>10}{'delta':>9}{'ratio':>8}")
    rows = [("fpts", mu, actual)]
    if "projected_toi" in data:
        rows.append(("toi (min)", data["projected_toi"], data["actual_toi"]))
        for stat in STAT_COLUMNS:
            rows.append((stat, data[f"projected_{stat}"], data[f"actual_{stat}"]))
    for label, pred, act in rows:
        ratio = pred.mean() / act.mean() if act.mean() else float("nan")
        print(f"  {label:<12}{pred.mean():11.3f}{act.mean():10.3f}"
              f"{act.mean() - pred.mean():+9.3f}{ratio:8.2f}")
    print()


def _fit_calibration(mu, actual):
    """Least-squares affine map from raw projection onto the actual scale.

    Split half-and-half by player so the reported improvement is out-of-sample
    with respect to the players it was fitted on. Fitting and evaluating a
    two-parameter map on the same 10k rows would overstate the gain.
    """
    coef = np.polyfit(mu, actual, 1)
    slope, intercept = float(coef[0]), float(coef[1])
    return intercept, slope


def _apply_calibration(mu, intercept, slope):
    return np.maximum(0.0, intercept + slope * mu)


def _accuracy(actual, predicted):
    err = predicted - actual
    return {
        "bias": float(err.mean()),
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err**2).mean())),
    }


def _report_calibration_fit(mu, actual, nhl_id, rng) -> tuple[float, float]:
    """Fit the bias correction and say plainly whether it helps."""
    # Held-out split by player, so a player never appears on both sides.
    players = np.unique(nhl_id)
    rng.shuffle(players)
    train_players = set(players[: len(players) // 2].tolist())
    train = np.array([p in train_players for p in nhl_id])
    test = ~train

    intercept, slope = _fit_calibration(mu[train], actual[train])
    full_intercept, full_slope = _fit_calibration(mu, actual)

    raw = _accuracy(actual[test], mu[test])
    calibrated = _accuracy(actual[test], _apply_calibration(mu[test], intercept, slope))

    print("Calibration fit  (actual ~ intercept + slope * projected)")
    print(f"  fitted on half the players: intercept={intercept:+.4f} slope={slope:.4f}")
    print(f"  fitted on all players:      intercept={full_intercept:+.4f} slope={full_slope:.4f}")
    print()
    print("  Held-out accuracy, raw vs calibrated")
    print(f"  {'':<12}{'bias':>9}{'MAE':>9}{'RMSE':>9}")
    print(f"  {'raw':<12}{raw['bias']:+9.3f}{raw['mae']:9.3f}{raw['rmse']:9.3f}")
    print(f"  {'calibrated':<12}{calibrated['bias']:+9.3f}"
          f"{calibrated['mae']:9.3f}{calibrated['rmse']:9.3f}")
    better = calibrated["rmse"] < raw["rmse"] and abs(calibrated["bias"]) < abs(raw["bias"])
    print(f"  -> calibration {'improves' if better else 'does NOT improve'} the projection")
    print()
    return full_intercept, full_slope


def fit(path: Path, seed: int = 20260802) -> None:
    data = _read_residuals(path)
    raw_mu, actual = data["projected_fpts"], data["actual_fpts"]
    nhl_id, game_date, team_id = data["nhl_id"], data["game_date"], data["team_id"]
    rng = np.random.default_rng(seed)

    print(f"{len(raw_mu)} player-games")
    print(f"  mean projected {raw_mu.mean():.2f}, mean actual {actual.mean():.2f}")
    print(f"  bias (actual - projected) {(actual - raw_mu).mean():+.3f}")
    print(f"  projected FPTS range {raw_mu.min():.2f} to {raw_mu.max():.2f}")
    print()

    _report_bias(data)

    intercept, slope = _report_calibration_fit(raw_mu, actual, nhl_id, rng)

    # Everything below is fitted against the CALIBRATED projection, because
    # that is what the optimizer will actually see. The two have to be fitted
    # together or P(win) silently decalibrates.
    mu = _apply_calibration(raw_mu, intercept, slope)
    resid = actual - mu

    print("=== sigma fitted against the CALIBRATED projection ===")
    print(f"  residual mean {resid.mean():+.4f}, sd {resid.std(ddof=1):.3f}")
    print()

    buckets = _bucket_sigmas(mu, resid)
    print("Bucketed residual sd by calibrated projected FPTS")
    print(f"  {'mu':>7} {'sigma':>7} {'sigma/mu':>9} {'n':>7}")
    for b_mu, b_sig, n in buckets:
        ratio = b_sig / b_mu if b_mu > 0.01 else float("nan")
        print(f"  {b_mu:7.2f} {b_sig:7.2f} {ratio:9.2f} {n:7d}")
    print()

    fits = _fit_forms(buckets)
    print("Candidate forms (weighted RMSE against bucket sigmas)")
    for name in ("constant_cv", "affine", "power"):
        f = fits[name]
        params = ", ".join(f"{k}={v:.4f}" for k, v in f["params"].items())
        print(f"  {name:12s} rmse={f['rmse']:.4f}  {params}")
    best = min(fits, key=lambda n: fits[n]["rmse"])
    print(f"  -> best: {best}")
    print()

    print("Coverage: fraction of synthetic weekly totals inside the 80% interval")
    print("  No debiasing this time — the calibration above is the fix.")
    for name in ("constant_cv", "affine", "power"):
        cov = _calibration(mu, resid, fits[name]["predict"], nhl_id, game_date, rng)
        flag = "OK" if 0.75 <= cov <= 0.85 else "OUT OF BAND"
        print(f"  {name:12s} coverage={cov:.3f}  [{flag}]")
    print()

    corr, n_pairs = _linemate_correlation(mu, resid, nhl_id, game_date, team_id)
    if corr is None:
        print("Linemate correlation: not enough repeated same-team pairs to measure")
    else:
        verdict = "material" if abs(corr) > 0.15 else "negligible, keep the independent sum"
        print(f"Linemate correlation: mean r={corr:+.4f} over {n_pairs} pairs -> {verdict}")
    print()

    print("Paste into src/predict/forecasting/calibration.py:")
    print(f"  CALIBRATION_INTERCEPT = {intercept:.4f}")
    print(f"  CALIBRATION_SLOPE = {slope:.4f}")
    print("Paste into src/optimize/week/variance.py:")
    affine = fits["affine"]["params"]
    print(f"  SKATER_SIGMA_INTERCEPT = {affine['a']:.4f}")
    print(f"  SKATER_SIGMA_SLOPE = {affine['b']:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    h = sub.add_parser("harvest", help="score player-games and write residuals")
    h.add_argument("--season", default="20252026")
    h.add_argument("--dates", type=int, default=45, help="game dates to sample (0 = all)")
    h.add_argument("--out", type=Path, default=RESIDUALS_PATH)
    h.add_argument(
        "--min-toi", type=int, default=DEFAULT_MIN_TOI_SECONDS,
        help="minimum 5v5 seconds in the measured game (low = include the streaming pool)",
    )

    f = sub.add_parser("fit", help="fit sigma(mu) to harvested residuals")
    f.add_argument("--path", type=Path, default=RESIDUALS_PATH)

    args = parser.parse_args()
    if args.command == "harvest":
        harvest(args.season, args.dates or None, args.out, args.min_toi)
    else:
        fit(args.path)


if __name__ == "__main__":
    main()

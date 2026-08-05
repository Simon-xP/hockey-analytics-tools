"""Fit `outcome_var`: the variance of fantasy points given a goalie start.

Produces the single global constant in
`src/predict/goalies/constants.py::OUTCOME_VAR`, plus the diagnostics the
brief asks for.

## Why walk-forward, and why accumulators

The quantity being fitted is a **predictive** residual, not a raw
historical spread. It has to include the model's own error, because that
error is a real part of the uncertainty the optimizer should reflect.

So every projection must be made without seeing its own game. Rather than
issuing date-gated SQL per start (correct but slow, and easy to get subtly
wrong), this walks the season in chronological order carrying running
accumulators, and updates them only *after* a date is scored. Leakage is
then structurally impossible rather than something to remember.

The projections come from `project_start_value`, the same pure function
the live path uses, so there is no train/serve skew.

## Why the variance of the total

Never by summing per-stat variances. Goalie stats are heavily correlated
through the same game: saves and goals against both scale with shots
faced, wins move inversely with goals against, and a shutout is literally
goals against equal to zero. Summing independent per-stat variances
ignores all of that and is wrong by an unpredictable amount.

Usage:
    python -m scripts.fit_goalie_variance
    python -m scripts.fit_goalie_variance --test-seasons 20242025 20252026
    python -m scripts.fit_goalie_variance --derive-shrinkage
"""

import argparse
from collections import defaultdict, deque
from dataclasses import dataclass

import numpy as np
from sqlalchemy import text

from src.core.db import get_session, init_db
from src.predict.goalies.constants import (
    DEFAULT_GOALS_FOR_PER_GAME,
    DEFAULT_SAVE_RATE,
    DEFAULT_SHOTS_AGAINST_PER_START,
    LEAGUE_LEVEL_CREDIBILITY_STARTS,
    LOOKBACK_SEASONS,
    SAVE_RATE_CREDIBILITY_SHOTS,
    TEAM_RATE_CREDIBILITY_GAMES,
    TEAM_RATE_CREDIBILITY_SHOTS,
    lookback_start,
)
from src.predict.goalies.save_quality import credibility_weight, shrink_save_rate
from src.predict.goalies.start_value import StartInputs, project_start_value

# How much history feeds the accumulators before scoring begins. Three
# seasons matches the live path's lookback.
WARMUP_SEASONS = 3


@dataclass
class Accumulator:
    """Running totals, only ever updated with games already scored."""

    shots: float = 0.0
    saves: float = 0.0
    goals_for: float = 0.0
    goals_against: float = 0.0
    starts: int = 0

    def add(self, shots, saves, gf, ga, sign=1):
        self.shots += sign * shots
        self.saves += sign * saves
        self.goals_for += sign * gf
        self.goals_against += sign * ga
        self.starts += sign


class LeagueState:
    """Everything the model needs to know about the world so far."""

    def __init__(self):
        self.goalie = defaultdict(Accumulator)
        self.team_def = defaultdict(Accumulator)   # shots the team allowed
        self.team_off = defaultdict(Accumulator)   # shots the team generated
        self.league = Accumulator()
        # League level for the current season only. The window accumulator
        # lags a drifting league; this one tracks it. See
        # LEAGUE_LEVEL_CREDIBILITY_STARTS.
        self.season_league = Accumulator()
        self._season = None
        # Observed starts still inside the lookback window, oldest first.
        # Kept so the window can be *closed* as well as opened, which is
        # what makes the fitter match the live path's `game_date >= since`.
        self._window: deque = deque()

    @staticmethod
    def _rates(acc: Accumulator):
        return (
            acc.shots / acc.starts,
            acc.saves / acc.shots if acc.shots else DEFAULT_SAVE_RATE,
            acc.goals_for / acc.starts,
        )

    def league_rates(self):
        """Current league level, plus the window save rate for era adjustment.

        Mirrors `start_value._league_rates` exactly. The first three track
        the league now; the fourth says which era the windowed goalie and
        team numbers were earned in.
        """
        if self.league.starts == 0:
            d = (DEFAULT_SHOTS_AGAINST_PER_START, DEFAULT_SAVE_RATE,
                 DEFAULT_GOALS_FOR_PER_GAME)
            return (*d, d)

        window = self._rates(self.league)
        if self.season_league.starts == 0:
            return (*window, window)

        s_sa, s_save, s_gf = self._rates(self.season_league)
        z = credibility_weight(self.season_league.starts,
                               LEAGUE_LEVEL_CREDIBILITY_STARTS)
        return (
            z * s_sa + (1 - z) * window[0],
            z * s_save + (1 - z) * window[1],
            z * s_gf + (1 - z) * window[2],
            window,
        )

    def blend(self, value: float | None, league: float, games: int) -> float:
        if value is None or games == 0:
            return league
        z = credibility_weight(games, TEAM_RATE_CREDIBILITY_GAMES)
        return z * value + (1 - z) * league

    def build_inputs(self, row) -> StartInputs:
        """Assemble model inputs from state that predates this game."""
        lg_sa, lg_save, lg_gf, window = self.league_rates()
        window_sa, window_save, window_gf = window

        own_def = self.team_def[row["team_id"]]
        opp_off = self.team_off[row["opponent_team_id"]]
        gk = self.goalie[row["goalie_id"]]

        quality = shrink_save_rate(
            goalie_shots=gk.shots, goalie_saves=gk.saves,
            team_shots=own_def.shots, team_saves=own_def.saves,
            league_rate=window_save, current_league_rate=lg_save,
            k_goalie=SAVE_RATE_CREDIBILITY_SHOTS,
            k_team=TEAM_RATE_CREDIBILITY_SHOTS,
        )

        return StartInputs(
            team_sa_per_start=self.blend(
                own_def.shots / own_def.starts if own_def.starts else None,
                window_sa, own_def.starts),
            opp_sf_per_game=self.blend(
                opp_off.shots / opp_off.starts if opp_off.starts else None,
                window_sa, opp_off.starts),
            league_sa_per_start=lg_sa,
            window_sa_per_start=window_sa,
            save_rate=quality.save_rate,
            league_save_rate=lg_save,
            own_gf_per_game=self.blend(
                own_def.goals_for / own_def.starts if own_def.starts else None,
                window_gf, own_def.starts),
            opp_ga_per_game=self.blend(
                opp_off.goals_against / opp_off.starts if opp_off.starts else None,
                window_gf, opp_off.starts),
            own_ga_per_game=self.blend(
                own_def.goals_against / own_def.starts if own_def.starts else None,
                window_gf, own_def.starts),
            opp_gf_per_game=self.blend(
                opp_off.goals_for / opp_off.starts if opp_off.starts else None,
                window_gf, opp_off.starts),
            league_gf_per_game=lg_gf,
            window_gf_per_game=window_gf,
            is_home=row["is_home"],
            save_credibility=quality.credibility,
            team_games_seen=own_def.starts,
        )

    def _apply(self, row, sign: int):
        shots = row["shots_against"]
        saves = row["saves"]
        gf = row["team_score"] or 0
        ga = row["opponent_score"] or 0

        self.goalie[row["goalie_id"]].add(shots, saves, gf, ga, sign)
        self.team_def[row["team_id"]].add(shots, saves, gf, ga, sign)
        # From the opponent's perspective these shots are offense generated.
        self.team_off[row["opponent_team_id"]].add(shots, saves, ga, gf, sign)
        self.league.add(shots, saves, gf, ga, sign)

    def observe(self, row):
        """Fold a completed start into state. Called only after scoring."""
        if row["season"] != self._season:
            self._season = row["season"]
            self.season_league = Accumulator()
        self._apply(row, +1)
        self.season_league.add(
            row["shots_against"], row["saves"],
            row["team_score"] or 0, row["opponent_score"] or 0,
        )
        self._window.append(row)

    def expire(self, as_of):
        """Drop games that fall outside the lookback window at `as_of`.

        Without this the accumulators carry every season ever played, and
        the model inherits a higher-scoring era: measured over-projection
        of 0.93 points per start on 2024-26. The live path bounds the same
        rates with `game_date >= since`, so the fitter must too or the
        fitted variance describes a different model than the one served.
        """
        since = lookback_start(as_of, LOOKBACK_SEASONS)
        while self._window and self._window[0]["game_date"] < since:
            self._apply(self._window.popleft(), -1)


def load_starts(session, seasons: list[str] | None = None) -> list[dict]:
    """Every start, oldest first."""
    where = "is_start AND shots_against > 0"
    params: dict = {}
    if seasons:
        where += " AND season = ANY(:seasons)"
        params["seasons"] = seasons

    rows = session.execute(
        text(f"""
            SELECT game_id, goalie_id, team_id, opponent_team_id, game_date,
                   season, is_home, shots_against, saves, goals_against,
                   team_score, opponent_score, decision, shutout, fpts
            FROM goalie_game_log
            WHERE {where}
            ORDER BY game_date, game_id
        """),
        params,
    ).fetchall()

    cols = ["game_id", "goalie_id", "team_id", "opponent_team_id", "game_date",
            "season", "is_home", "shots_against", "saves", "goals_against",
            "team_score", "opponent_score", "decision", "shutout", "fpts"]
    return [dict(zip(cols, r)) for r in rows]


def walk_forward(all_starts: list[dict], test_seasons: set[str]) -> list[dict]:
    """Project every test-season start using only prior games.

    Returns records with projected and actual points.
    """
    state = LeagueState()
    results = []

    # Group by date so same-day games cannot see each other.
    by_date: dict = defaultdict(list)
    for row in all_starts:
        by_date[row["game_date"]].append(row)

    for game_date in sorted(by_date):
        same_day = by_date[game_date]
        state.expire(game_date)

        for row in same_day:
            if row["season"] in test_seasons:
                projection = project_start_value(state.build_inputs(row))
                results.append({
                    **row,
                    "projected": projection.start_value,
                    "proj_shots": projection.shots_against,
                    "proj_ga": projection.goals_against,
                    "proj_win": projection.win_prob,
                    "actual": row["fpts"],
                })

        # State advances only after the whole day is scored.
        for row in same_day:
            state.observe(row)

    return results


def report_fit(results: list[dict]) -> dict:
    """Fit the constant and run the diagnostics the brief requires."""
    projected = np.array([r["projected"] for r in results], float)
    actual = np.array([r["actual"] for r in results], float)
    residual = actual - projected

    n = len(residual)
    bias = float(residual.mean())
    outcome_var = float(residual.var(ddof=1))
    mae = float(np.abs(residual).mean())

    print(f"\n{'='*66}")
    print(f"OUTCOME VARIANCE FIT  ({n:,} walk-forward starts)")
    print(f"{'='*66}")
    print(f"  mean actual      {actual.mean():7.3f}")
    print(f"  mean projected   {projected.mean():7.3f}")
    print(f"  bias             {bias:+7.3f}")
    print(f"  MAE              {mae:7.3f}")
    print(f"  residual sd      {np.sqrt(outcome_var):7.3f}")
    print(f"  OUTCOME_VAR      {outcome_var:7.3f}")

    # Baselines. A season-relative comparison, because the league level
    # moves and a global mean would flatter the model for the wrong reason.
    naive = float(((actual - actual.mean()) ** 2).mean())
    print(f"\n  variance around the season mean: {naive:.3f}")
    print(f"  variance explained by the model: "
          f"{100 * (1 - outcome_var / naive):.1f}%")

    # --- Is the constant really the right shape? -------------------------
    # Fit residual spread against projection and confirm the slope cannot
    # be distinguished from zero, as the brief asks.
    print(f"\n{'-'*66}")
    print("Residual spread by projection quintile")
    print(f"{'-'*66}")
    print(f"  {'quintile':<10}{'n':>7}{'proj mean':>12}{'resid sd':>11}"
          f"{'resid var':>12}")

    order = np.argsort(projected)
    buckets = np.array_split(order, 5)
    bucket_mid, bucket_sd = [], []
    for i, idx in enumerate(buckets):
        p_mean = projected[idx].mean()
        r_sd = residual[idx].std(ddof=1)
        bucket_mid.append(p_mean)
        bucket_sd.append(r_sd)
        print(f"  Q{i+1:<9}{len(idx):>7}{p_mean:>12.3f}{r_sd:>11.3f}"
              f"{r_sd**2:>12.3f}")

    slope, intercept = np.polyfit(bucket_mid, bucket_sd, 1)
    # Bootstrap the slope to see whether zero is a plausible value.
    rng = np.random.default_rng(42)
    slopes = []
    for _ in range(2000):
        pick = rng.integers(0, n, n)
        o = np.argsort(projected[pick])
        bs_mid, bs_sd = [], []
        for idx in np.array_split(o, 5):
            bs_mid.append(projected[pick][idx].mean())
            bs_sd.append(residual[pick][idx].std(ddof=1))
        slopes.append(np.polyfit(bs_mid, bs_sd, 1)[0])
    lo, hi = np.percentile(slopes, [2.5, 97.5])

    print(f"\n  affine fit: sd = {intercept:.3f} + {slope:.4f} * projection")
    print(f"  slope 95% CI: [{lo:.4f}, {hi:.4f}]")
    if lo <= 0.0 <= hi:
        print("  -> slope is not distinguishable from zero. "
              "The global constant is the right shape.")
    else:
        print("  -> slope IS distinguishable from zero. "
              "Revisit whether a constant is appropriate.")

    # --- Calibration, which can veto the work ---------------------------
    print(f"\n{'-'*66}")
    print("Calibration of the conditional (given-a-start) distribution")
    print(f"{'-'*66}")
    sd = np.sqrt(outcome_var)
    for coverage, z in [(0.50, 0.6745), (0.80, 1.2816), (0.95, 1.9600)]:
        inside = np.mean(np.abs(residual) <= z * sd)
        print(f"  nominal {coverage:.0%}  ->  actual {inside:.3f}")

    inside_80 = float(np.mean(np.abs(residual) <= 1.2816 * sd))

    return {
        "n": n,
        "outcome_var": outcome_var,
        "residual_sd": float(np.sqrt(outcome_var)),
        "bias": bias,
        "mae": mae,
        "coverage_80": inside_80,
        "affine_slope": float(slope),
        "affine_slope_ci": (float(lo), float(hi)),
        "bucket_var": [float(s**2) for s in bucket_sd],
    }


def derive_shrinkage(session) -> None:
    """Re-derive the save-rate credibility constant from the data."""
    rows = session.execute(text("""
        SELECT season, goalie_id, SUM(shots_against), SUM(saves)
        FROM goalie_game_log WHERE is_start
        GROUP BY 1, 2 HAVING SUM(shots_against) >= 300
    """)).fetchall()

    by_season = defaultdict(list)
    for season, _gid, sa, sv in rows:
        by_season[season].append((float(sa), float(sv)))

    print(f"\n{'season':<10}{'n':>5}{'lg sv%':>9}{'sd_obs':>9}"
          f"{'sd_binom':>10}{'sd_true':>9}{'k':>9}")
    ks = []
    for season in sorted(by_season):
        data = by_season[season]
        sa = np.array([d[0] for d in data])
        sv = np.array([d[1] for d in data])
        rate = sv / sa
        p = sv.sum() / sa.sum()
        var_obs = np.average((rate - p) ** 2, weights=sa)
        var_binom = p * (1 - p) * np.average(1.0 / sa, weights=sa)
        var_true = var_obs - var_binom
        if var_true <= 0:
            print(f"{season:<10}{len(data):>5}{p:>9.4f}{np.sqrt(var_obs):>9.4f}"
                  f"{np.sqrt(var_binom):>10.4f}{'~0':>9}{'undef':>9}")
            continue
        k = p * (1 - p) / var_true
        ks.append((season, k))
        print(f"{season:<10}{len(data):>5}{p:>9.4f}{np.sqrt(var_obs):>9.4f}"
              f"{np.sqrt(var_binom):>10.4f}{np.sqrt(var_true):>9.4f}{k:>9.0f}")

    recent = [k for s, k in ks][-5:]
    median_k = float(np.median(recent))
    print(f"\n  median k over the last 5 seasons: {median_k:.0f} shots")
    print(f"  weight on own rate after 10 starts (280 shots): "
          f"{280 / (280 + median_k):.3f}")
    print(f"  weight after 50 starts (1400 shots): "
          f"{1400 / (1400 + median_k):.3f}")
    print(f"  configured SAVE_RATE_CREDIBILITY_SHOTS = "
          f"{SAVE_RATE_CREDIBILITY_SHOTS:.0f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fit goalie outcome variance")
    parser.add_argument("--test-seasons", nargs="+",
                        default=["20242025", "20252026"])
    parser.add_argument("--derive-shrinkage", action="store_true")
    args = parser.parse_args()
    init_db()

    with get_session() as session:
        if args.derive_shrinkage:
            derive_shrinkage(session)
            raise SystemExit(0)

        print("Loading starts...")
        all_starts = load_starts(session)
        print(f"  {len(all_starts):,} starts across "
              f"{len({s['season'] for s in all_starts})} seasons")

    test = set(args.test_seasons)
    print(f"\nWalk-forward over {sorted(test)} "
          f"(state warmed on everything prior)...")
    results = walk_forward(all_starts, test)
    print(f"  {len(results):,} projections made")

    summary = report_fit(results)

    print(f"\n{'='*66}")
    print(f"Set OUTCOME_VAR = {summary['outcome_var']:.2f}")
    print(f"Fitted on: {', '.join(sorted(test))}, walk-forward, "
          f"{summary['n']:,} starts")
    print(f"{'='*66}")

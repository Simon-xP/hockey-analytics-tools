"""RAPM validation suite.

Split-half reliability, predictive power, and sanity checks.
See docs/rapm-design.md Section 6.
"""

import logging
from dataclasses import dataclass

import numpy as np
from scipy.stats import pearsonr
from sklearn.linear_model import Ridge

from .model import build_design_matrix, cross_validate_lambda

logger = logging.getLogger(__name__)


@dataclass
class SplitHalfResult:
    correlation: float
    p_value: float
    n_players: int
    min_toi_minutes: float


@dataclass
class PredictiveResult:
    correlation: float
    p_value: float
    rmse_rapm: float
    rmse_raw: float
    rmse_baseline: float
    n_players: int


def split_half_reliability(
    segments: list[dict],
    min_toi_minutes: float = 200,
    cv_lambdas: list[float] | None = None,
) -> SplitHalfResult:
    """Split-half reliability test.

    Splits segments into odd/even games, fits RAPM independently on each
    half, and computes Pearson correlation of offensive ratings for
    players qualifying in both halves.
    """
    game_ids = sorted(set(seg["game_id"] for seg in segments))
    odd_games = set(game_ids[::2])
    even_games = set(game_ids[1::2])

    odd_segs = [s for s in segments if s["game_id"] in odd_games]
    even_segs = [s for s in segments if s["game_id"] in even_games]

    logger.info(
        "Split-half: %d odd-game segments, %d even-game segments",
        len(odd_segs), len(even_segs),
    )

    X_odd, y_odd, _, w_odd, pids_odd, _, gids_odd = build_design_matrix(
        odd_segs, min_toi_minutes=min_toi_minutes
    )
    X_even, y_even, _, w_even, pids_even, _, gids_even = build_design_matrix(
        even_segs, min_toi_minutes=min_toi_minutes
    )

    common = set(pids_odd) & set(pids_even)
    logger.info(
        "Odd: %d players, Even: %d players, Common: %d",
        len(pids_odd), len(pids_even), len(common),
    )
    if len(common) < 10:
        raise ValueError(f"Only {len(common)} common players — not enough for correlation")

    lam_odd = cross_validate_lambda(
        X_odd, y_odd, w_odd, gids_odd, len(pids_odd), cv_lambdas
    )
    model_odd = Ridge(alpha=lam_odd, fit_intercept=False)
    model_odd.fit(X_odd, y_odd, sample_weight=w_odd)

    lam_even = cross_validate_lambda(
        X_even, y_even, w_even, gids_even, len(pids_even), cv_lambdas
    )
    model_even = Ridge(alpha=lam_even, fit_intercept=False)
    model_even.fit(X_even, y_even, sample_weight=w_even)

    odd_ratings = {pid: float(model_odd.coef_[i]) for i, pid in enumerate(pids_odd)}
    even_ratings = {pid: float(model_even.coef_[i]) for i, pid in enumerate(pids_even)}

    common_sorted = sorted(common)
    r_odd = np.array([odd_ratings[p] for p in common_sorted])
    r_even = np.array([even_ratings[p] for p in common_sorted])

    corr, pval = pearsonr(r_odd, r_even)
    logger.info("Split-half correlation: r=%.4f (p=%.2e, n=%d)", corr, pval, len(common))

    return SplitHalfResult(
        correlation=float(corr),
        p_value=float(pval),
        n_players=len(common),
        min_toi_minutes=min_toi_minutes,
    )


def predictive_power(
    train_segments: list[dict],
    test_segments: list[dict],
    min_toi_minutes: float = 400,
    cv_lambdas: list[float] | None = None,
) -> PredictiveResult:
    """Predictive power test: train on past seasons, predict next season.

    Compares RAPM ratings to raw on-ice xGF/60 rates and league average
    baseline as predictors of test-period performance.
    """
    X_train, y_train, _, w_train, pids_train, _, gids_train = build_design_matrix(
        train_segments, min_toi_minutes=min_toi_minutes
    )

    lam = cross_validate_lambda(
        X_train, y_train, w_train, gids_train, len(pids_train), cv_lambdas
    )
    model = Ridge(alpha=lam, fit_intercept=False)
    model.fit(X_train, y_train, sample_weight=w_train)

    rapm_ratings = {pid: float(model.coef_[i]) for i, pid in enumerate(pids_train)}

    # Compute raw on-ice xGF/60 for each player in training data
    player_xgf = {}
    player_toi = {}
    for seg in train_segments:
        dur_min = seg["duration_seconds"] / 60.0
        if dur_min <= 0:
            continue
        xgf60_home = (seg["home_xgf"] / dur_min) * 60
        xgf60_away = (seg["away_xgf"] / dur_min) * 60

        for pid in seg["home_skater_ids"]:
            player_xgf[pid] = player_xgf.get(pid, 0.0) + xgf60_home * dur_min
            player_toi[pid] = player_toi.get(pid, 0.0) + dur_min
        for pid in seg["away_skater_ids"]:
            player_xgf[pid] = player_xgf.get(pid, 0.0) + xgf60_away * dur_min
            player_toi[pid] = player_toi.get(pid, 0.0) + dur_min

    raw_rates = {
        pid: player_xgf[pid] / player_toi[pid]
        for pid in player_xgf
        if player_toi.get(pid, 0) >= min_toi_minutes
    }

    # Compute actual test-period on-ice xGF/60 per player
    test_xgf = {}
    test_toi = {}
    for seg in test_segments:
        dur_min = seg["duration_seconds"] / 60.0
        if dur_min <= 0:
            continue
        xgf60_home = (seg["home_xgf"] / dur_min) * 60
        xgf60_away = (seg["away_xgf"] / dur_min) * 60

        for pid in seg["home_skater_ids"]:
            test_xgf[pid] = test_xgf.get(pid, 0.0) + xgf60_home * dur_min
            test_toi[pid] = test_toi.get(pid, 0.0) + dur_min
        for pid in seg["away_skater_ids"]:
            test_xgf[pid] = test_xgf.get(pid, 0.0) + xgf60_away * dur_min
            test_toi[pid] = test_toi.get(pid, 0.0) + dur_min

    test_rates = {
        pid: test_xgf[pid] / test_toi[pid]
        for pid in test_xgf
        if test_toi.get(pid, 0) >= min_toi_minutes
    }

    common = sorted(set(rapm_ratings) & set(raw_rates) & set(test_rates))
    logger.info("Predictive test: %d common players", len(common))

    if len(common) < 10:
        raise ValueError(f"Only {len(common)} common players")

    actual = np.array([test_rates[p] for p in common])
    pred_rapm = np.array([rapm_ratings[p] for p in common])
    pred_raw = np.array([raw_rates[p] for p in common])
    pred_baseline = np.full(len(common), actual.mean())

    corr, pval = pearsonr(pred_rapm, actual)

    rmse_rapm = float(np.sqrt(np.mean((actual - pred_rapm) ** 2)))
    rmse_raw = float(np.sqrt(np.mean((actual - pred_raw) ** 2)))
    rmse_baseline = float(np.sqrt(np.mean((actual - pred_baseline) ** 2)))

    logger.info(
        "Predictive: r=%.4f, RMSE rapm=%.3f raw=%.3f baseline=%.3f",
        corr, rmse_rapm, rmse_raw, rmse_baseline,
    )

    return PredictiveResult(
        correlation=float(corr),
        p_value=float(pval),
        rmse_rapm=rmse_rapm,
        rmse_raw=rmse_raw,
        rmse_baseline=rmse_baseline,
        n_players=len(common),
    )

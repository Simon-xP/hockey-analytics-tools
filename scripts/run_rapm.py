"""Run the RAPM pipeline: fit model, compute elevation, persist, validate.

Usage:
    python -m scripts.run_rapm                    # fit + persist (default seasons)
    python -m scripts.run_rapm --validate         # fit + persist + validation
    python -m scripts.run_rapm --validate-only    # validation only (no persist)
"""

import argparse
import logging
import time

import numpy as np
from sklearn.linear_model import Ridge

from src.core.db import get_session
from src.analytics.rapm.model import (
    RAPMResult,
    build_design_matrix,
    cross_validate_lambda,
    load_segments,
    persist_ratings,
)
from src.analytics.rapm.elevation import compute_elevation

logger = logging.getLogger(__name__)

SEASONS = [
    ("2022-10-01", "2023-07-01"),
    ("2023-10-01", "2024-07-01"),
    ("2024-10-01", "2025-07-01"),
    ("2025-10-01", "2026-07-01"),
]

MODEL_VERSION = "rapm_v1_5v5"
SEASONS_LABEL = ",".join(
    f"{s[:4]}{int(e[:4])}" for s, e in SEASONS
)


def _load_all_segments(session):
    all_segments = []
    for start, end in SEASONS:
        segs = load_segments(session, start, end)
        logger.info("  %s to %s: %d segments", start, end, len(segs))
        all_segments.extend(segs)
    logger.info("Total: %d segments", len(all_segments))
    return all_segments


def fit_and_persist(session, all_segments):
    t0 = time.time()

    X, y_off, y_def, weights, player_ids, toi_minutes, game_ids = (
        build_design_matrix(all_segments)
    )
    n_players = len(player_ids)
    logger.info("Design matrix: %d rows, %d players", X.shape[0], n_players)

    logger.info("Cross-validating offensive model...")
    lam_off = cross_validate_lambda(X, y_off, weights, game_ids, n_players)
    model_off = Ridge(alpha=lam_off, fit_intercept=False)
    model_off.fit(X, y_off, sample_weight=weights)

    logger.info("Cross-validating defensive model...")
    lam_def = cross_validate_lambda(X, y_def, weights, game_ids, n_players)
    model_def = Ridge(alpha=lam_def, fit_intercept=False)
    model_def.fit(X, y_def, sample_weight=weights)

    result = RAPMResult(
        player_ids=player_ids,
        ratings_off=model_off.coef_[:n_players],
        ratings_def=model_def.coef_[:n_players],
        toi_minutes=toi_minutes,
        home_ice_coef_off=float(model_off.coef_[n_players]),
        home_ice_coef_def=float(model_def.coef_[n_players]),
        best_lambda_off=lam_off,
        best_lambda_def=lam_def,
        n_segments=len(all_segments),
        n_players=n_players,
    )

    logger.info("Home ice advantage: off=%.3f, def=%.3f",
                result.home_ice_coef_off, result.home_ice_coef_def)

    ratings_off = {pid: float(model_off.coef_[i]) for i, pid in enumerate(player_ids)}
    logger.info("Computing WOWY elevation...")
    elevations = compute_elevation(all_segments, ratings_off, player_ids)
    elev_scores = {pid: e.elevation_score for pid, e in elevations.items()}
    logger.info("Elevation computed for %d players", len(elev_scores))

    n = persist_ratings(session, result, MODEL_VERSION, SEASONS_LABEL, elev_scores)
    session.commit()

    elapsed = time.time() - t0
    logger.info("Fit + persist complete: %d players in %.1fs", n, elapsed)
    return result


def run_validation(session):
    from src.analytics.rapm.validation import split_half_reliability, predictive_power

    logger.info("=== VALIDATION ===")

    all_segments = _load_all_segments(session)

    logger.info("\n--- Split-Half Reliability ---")
    sh = split_half_reliability(all_segments, min_toi_minutes=200)
    logger.info("  Correlation: r=%.4f (p=%.2e)", sh.correlation, sh.p_value)
    logger.info("  Players: %d (min %.0f min TOI in each half)", sh.n_players, sh.min_toi_minutes)
    if sh.correlation >= 0.4:
        logger.info("  PASS (r >= 0.4)")
    elif sh.correlation >= 0.3:
        logger.info("  MARGINAL (0.3 <= r < 0.4)")
    else:
        logger.warning("  FAIL (r < 0.3)")

    logger.info("\n--- Predictive Power ---")
    train_game_ids = set()
    for start, end in SEASONS[:3]:
        train_game_ids |= _game_ids_for_season(session, start, end)
    test_game_ids = set()
    for start, end in SEASONS[3:]:
        test_game_ids |= _game_ids_for_season(session, start, end)

    train_segs = [s for s in all_segments if s["game_id"] in train_game_ids]
    test_segs = [s for s in all_segments if s["game_id"] in test_game_ids]
    logger.info("  Train: %d segments, Test: %d segments", len(train_segs), len(test_segs))

    if not test_segs:
        logger.warning("  No test segments — skipping predictive test")
    else:
        pp = predictive_power(train_segs, test_segs, min_toi_minutes=400)
        logger.info("  Correlation: r=%.4f (p=%.2e)", pp.correlation, pp.p_value)
        logger.info("  RMSE: RAPM=%.3f  Raw=%.3f  Baseline=%.3f",
                     pp.rmse_rapm, pp.rmse_raw, pp.rmse_baseline)
        logger.info("  Players: %d", pp.n_players)
        if pp.correlation >= 0.2:
            logger.info("  PASS (r >= 0.2, statistically significant)")
        else:
            logger.warning("  FAIL (r < 0.2)")


def _game_ids_for_season(session, start, end):
    from sqlalchemy import text
    rows = session.execute(
        text("SELECT game_id FROM games WHERE date >= :s AND date < :e"),
        {"s": start, "e": end},
    ).fetchall()
    return {r[0] for r in rows}


def main():
    parser = argparse.ArgumentParser(description="Run RAPM pipeline")
    parser.add_argument("--validate", action="store_true",
                        help="Run validation after fitting")
    parser.add_argument("--validate-only", action="store_true",
                        help="Run validation only (no fit/persist)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    with get_session() as session:
        if args.validate_only:
            run_validation(session)
        else:
            logger.info("Loading segments...")
            all_segments = _load_all_segments(session)
            fit_and_persist(session, all_segments)
            if args.validate:
                run_validation(session)


if __name__ == "__main__":
    main()

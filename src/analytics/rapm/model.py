"""RAPM (Regularized Adjusted Plus-Minus) model.

One-sided ridge regression on shift segments. Each segment produces two
training rows (one per team) with only that team's 5 skaters as +1
indicators. Separate offensive (xGF/60) and defensive (xGA/60) models.

See docs/rapm-design.md for full specification.
"""

import logging
from dataclasses import dataclass, field

import numpy as np
from scipy import sparse
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.models.player_ratings import PlayerRating

logger = logging.getLogger(__name__)

MIN_TOI_MINUTES = 400
MIN_SEGMENT_SCORE_DIFF = 2


@dataclass
class RAPMResult:
    """Result of a RAPM model fit."""
    player_ids: list[int]
    ratings_off: np.ndarray
    ratings_def: np.ndarray | None
    toi_minutes: dict[int, float]
    home_ice_coef_off: float
    home_ice_coef_def: float | None
    best_lambda_off: float
    best_lambda_def: float | None
    n_segments: int
    n_players: int


def load_segments(
    session: Session,
    season_start: str,
    season_end: str,
) -> list[dict]:
    """Load 5v5 shift segments within score-state filter."""
    rows = session.execute(
        text("""
            SELECT ss.game_id, ss.period, ss.duration_seconds,
                   ss.home_skater_ids, ss.away_skater_ids,
                   ss.home_xgf, ss.away_xgf, ss.score_state
            FROM shift_segments ss
            JOIN games g ON ss.game_id = g.game_id
            WHERE ss.situation = '5v5'
              AND ABS(ss.score_state) <= :max_score_diff
              AND ss.duration_seconds >= 2
              AND g.date >= :start AND g.date < :end
        """),
        {
            "max_score_diff": MIN_SEGMENT_SCORE_DIFF,
            "start": season_start,
            "end": season_end,
        },
    ).fetchall()

    columns = [
        "game_id", "period", "duration_seconds",
        "home_skater_ids", "away_skater_ids",
        "home_xgf", "away_xgf", "score_state",
    ]
    return [dict(zip(columns, r)) for r in rows]


def build_design_matrix(
    segments: list[dict],
    min_toi_minutes: float = MIN_TOI_MINUTES,
) -> tuple[sparse.csr_matrix, np.ndarray, np.ndarray, np.ndarray, list[int], dict[int, float]]:
    """Build one-sided design matrix from shift segments.

    Each segment produces 2 rows: one from home perspective (home
    skaters = +1, response = home xGF/60) and one from away perspective
    (away skaters = +1, response = away xGF/60).

    Returns:
        X: sparse design matrix (2N x P+1), P player columns + is_home
        y_off: offensive response (xGF/60 for that team)
        y_def: defensive response (xGA/60 for that team)
        weights: segment duration in minutes (same for both rows from a segment)
        player_ids: ordered list of player IDs (column mapping)
        toi_minutes: total 5v5 TOI per player
        game_ids: game_id per row (for CV fold stratification)
    """
    player_toi = {}
    for seg in segments:
        dur_min = seg["duration_seconds"] / 60.0
        for pid in seg["home_skater_ids"]:
            player_toi[pid] = player_toi.get(pid, 0.0) + dur_min
        for pid in seg["away_skater_ids"]:
            player_toi[pid] = player_toi.get(pid, 0.0) + dur_min

    qualifying_players = {
        pid for pid, toi in player_toi.items() if toi >= min_toi_minutes
    }
    player_ids = sorted(qualifying_players)
    pid_to_col = {pid: i for i, pid in enumerate(player_ids)}
    n_players = len(player_ids)

    logger.info(
        "%d qualifying players (>= %.0f min) out of %d total",
        n_players, min_toi_minutes, len(player_toi),
    )

    n_segments = len(segments)
    n_rows = 2 * n_segments

    row_indices = []
    col_indices = []
    data_values = []
    y_off = np.zeros(n_rows)
    y_def = np.zeros(n_rows)
    weights = np.zeros(n_rows)
    game_ids = np.zeros(n_rows, dtype=np.int64)

    is_home_col = n_players

    for i, seg in enumerate(segments):
        dur_min = seg["duration_seconds"] / 60.0
        if dur_min <= 0:
            continue

        home_xgf_60 = (seg["home_xgf"] / dur_min) * 60
        away_xgf_60 = (seg["away_xgf"] / dur_min) * 60

        row_home = 2 * i
        row_away = 2 * i + 1

        # Home team row: response is home team's xGF/60
        y_off[row_home] = home_xgf_60
        y_def[row_home] = away_xgf_60  # what the opponent generated against home
        weights[row_home] = dur_min
        game_ids[row_home] = seg["game_id"]

        for pid in seg["home_skater_ids"]:
            if pid in pid_to_col:
                row_indices.append(row_home)
                col_indices.append(pid_to_col[pid])
                data_values.append(1.0)

        row_indices.append(row_home)
        col_indices.append(is_home_col)
        data_values.append(1.0)

        # Away team row: response is away team's xGF/60
        y_off[row_away] = away_xgf_60
        y_def[row_away] = home_xgf_60  # what the opponent generated against away
        weights[row_away] = dur_min
        game_ids[row_away] = seg["game_id"]

        for pid in seg["away_skater_ids"]:
            if pid in pid_to_col:
                row_indices.append(row_away)
                col_indices.append(pid_to_col[pid])
                data_values.append(1.0)

        # is_home = 0 for away (omitted, already zero)

    X = sparse.csr_matrix(
        (data_values, (row_indices, col_indices)),
        shape=(n_rows, n_players + 1),
    )

    qualified_toi = {pid: player_toi[pid] for pid in player_ids}
    return X, y_off, y_def, weights, player_ids, qualified_toi, game_ids


def cross_validate_lambda(
    X: sparse.csr_matrix,
    y: np.ndarray,
    weights: np.ndarray,
    game_ids: np.ndarray,
    n_players: int,
    lambdas: list[float] | None = None,
    n_folds: int = 5,
) -> float:
    """Find optimal lambda via game-stratified CV."""
    if lambdas is None:
        lambdas = [0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100]

    unique_games = np.unique(game_ids)
    game_to_group = {gid: i % n_folds for i, gid in enumerate(unique_games)}
    groups = np.array([game_to_group[gid] for gid in game_ids])

    best_lambda = lambdas[0]
    best_score = float("inf")

    for lam in lambdas:
        fold_errors = []

        for fold in range(n_folds):
            train_mask = groups != fold
            test_mask = groups == fold

            X_train = X[train_mask]
            y_train = y[train_mask]
            w_train = weights[train_mask]
            X_test = X[test_mask]
            y_test = y[test_mask]
            w_test = weights[test_mask]

            model = Ridge(alpha=lam, fit_intercept=False)
            model.fit(X_train, y_train, sample_weight=w_train)

            y_pred = model.predict(X_test)
            mse = np.average((y_test - y_pred) ** 2, weights=w_test)
            fold_errors.append(mse)

        mean_mse = np.mean(fold_errors)
        if mean_mse < best_score:
            best_score = mean_mse
            best_lambda = lam

    logger.info("Best lambda: %.4f (CV MSE: %.4f)", best_lambda, best_score)
    return best_lambda


def fit_rapm(
    session: Session,
    season_start: str,
    season_end: str,
    run_defensive: bool = True,
    cv_lambdas: list[float] | None = None,
) -> RAPMResult:
    """Fit the RAPM model on shift segments.

    Args:
        session: DB session for loading segments.
        season_start: Start date (inclusive).
        season_end: End date (exclusive).
        run_defensive: Whether to also fit the defensive model.
        cv_lambdas: Lambda grid for CV. None uses default.

    Returns:
        RAPMResult with player ratings.
    """
    logger.info("Loading segments from %s to %s...", season_start, season_end)
    segments = load_segments(session, season_start, season_end)
    logger.info("Loaded %d 5v5 segments", len(segments))

    if not segments:
        raise ValueError("No segments found")

    logger.info("Building design matrix...")
    X, y_off, y_def, weights, player_ids, toi_minutes, game_ids = (
        build_design_matrix(segments)
    )
    n_players = len(player_ids)
    logger.info(
        "Design matrix: %d rows x %d cols (%d players + is_home)",
        X.shape[0], X.shape[1], n_players,
    )

    # Offensive model
    logger.info("Cross-validating offensive model lambda...")
    best_lambda_off = cross_validate_lambda(
        X, y_off, weights, game_ids, n_players, cv_lambdas,
    )
    logger.info("Fitting offensive model (lambda=%.4f)...", best_lambda_off)
    model_off = Ridge(alpha=best_lambda_off, fit_intercept=False)
    model_off.fit(X, y_off, sample_weight=weights)

    ratings_off = model_off.coef_[:n_players]
    home_ice_off = model_off.coef_[n_players]
    logger.info(
        "Offensive model: home_ice=%.4f, rating range=[%.4f, %.4f]",
        home_ice_off, ratings_off.min(), ratings_off.max(),
    )

    # Defensive model
    ratings_def = None
    home_ice_def = None
    best_lambda_def = None

    if run_defensive:
        logger.info("Cross-validating defensive model lambda...")
        best_lambda_def = cross_validate_lambda(
            X, y_def, weights, game_ids, n_players, cv_lambdas,
        )
        logger.info("Fitting defensive model (lambda=%.4f)...", best_lambda_def)
        model_def = Ridge(alpha=best_lambda_def, fit_intercept=False)
        model_def.fit(X, y_def, sample_weight=weights)

        ratings_def = model_def.coef_[:n_players]
        home_ice_def = model_def.coef_[n_players]
        logger.info(
            "Defensive model: home_ice=%.4f, rating range=[%.4f, %.4f]",
            home_ice_def, ratings_def.min(), ratings_def.max(),
        )

    return RAPMResult(
        player_ids=player_ids,
        ratings_off=ratings_off,
        ratings_def=ratings_def,
        toi_minutes=toi_minutes,
        home_ice_coef_off=home_ice_off,
        home_ice_coef_def=home_ice_def,
        best_lambda_off=best_lambda_off,
        best_lambda_def=best_lambda_def,
        n_segments=len(segments),
        n_players=n_players,
    )


def persist_ratings(
    session: Session,
    result: RAPMResult,
    model_version: str,
    seasons_label: str,
    elevation_scores: dict[int, float] | None = None,
) -> int:
    """Save RAPM ratings to the player_ratings table.

    Upserts: existing rows for the same (player, model_version, seasons)
    are replaced.
    """
    session.execute(
        text(
            "DELETE FROM player_ratings "
            "WHERE model_version = :mv AND seasons = :s"
        ),
        {"mv": model_version, "s": seasons_label},
    )

    off_pctiles = _compute_percentiles(result.ratings_off)
    def_pctiles = (
        _compute_percentiles(-result.ratings_def)
        if result.ratings_def is not None
        else [None] * len(result.player_ids)
    )

    rows = []
    for i, pid in enumerate(result.player_ids):
        rows.append(PlayerRating(
            player_id=pid,
            model_version=model_version,
            seasons=seasons_label,
            rating_off=float(result.ratings_off[i]),
            rating_def=(
                float(result.ratings_def[i])
                if result.ratings_def is not None else None
            ),
            toi_minutes=result.toi_minutes[pid],
            percentile_off=int(off_pctiles[i]),
            percentile_def=(
                int(def_pctiles[i]) if def_pctiles[i] is not None else None
            ),
            elevation_off=(
                float(elevation_scores[pid])
                if elevation_scores and pid in elevation_scores else None
            ),
        ))

    session.add_all(rows)
    session.flush()
    return len(rows)


def _compute_percentiles(values: np.ndarray) -> list[int]:
    """Convert raw values to 1-100 percentile ranks."""
    from scipy.stats import rankdata
    ranks = rankdata(values, method="average")
    percentiles = (ranks / len(ranks)) * 100
    return [max(1, min(100, int(round(p)))) for p in percentiles]

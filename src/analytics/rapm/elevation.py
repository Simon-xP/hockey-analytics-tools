"""WOWY (With Or Without You) elevation metric.

Measures how much a player elevates their teammates' offensive production
beyond what RAPM's additive model predicts. Built on shift segments with
RAPM ratings as the baseline expectation.

See docs/rapm-design.md Section 5.2 for full specification.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

MIN_SHARED_MINUTES = 100
MIN_APART_MINUTES = 100
MIN_QUALIFYING_PAIRS = 3


@dataclass
class PairElevation:
    player_a: int
    player_b: int
    shared_toi: float
    apart_toi: float
    with_actual: float
    with_predicted: float
    without_actual: float
    without_predicted: float
    residual: float


@dataclass
class PlayerElevation:
    player_id: int
    elevation_score: float
    n_pairs: int
    pairs: list[PairElevation] = field(default_factory=list)


def _build_segment_arrays(
    segments: list[dict],
    qualifying: set[int],
) -> tuple[np.ndarray, np.ndarray, list[set[int]], list[set[int]]]:
    """Precompute per-segment xGF/60 and player sets.

    Returns:
        durations: segment durations in minutes
        xgf60: (N, 2) array — [home_xgf60, away_xgf60]
        home_sets: list of sets of qualifying home skater IDs
        away_sets: list of sets of qualifying away skater IDs
    """
    n = len(segments)
    durations = np.zeros(n)
    xgf60 = np.zeros((n, 2))
    home_sets = []
    away_sets = []

    for i, seg in enumerate(segments):
        dur_min = seg["duration_seconds"] / 60.0
        durations[i] = dur_min
        if dur_min > 0:
            xgf60[i, 0] = (seg["home_xgf"] / dur_min) * 60
            xgf60[i, 1] = (seg["away_xgf"] / dur_min) * 60
        home_sets.append(
            set(seg["home_skater_ids"]) & qualifying
        )
        away_sets.append(
            set(seg["away_skater_ids"]) & qualifying
        )

    return durations, xgf60, home_sets, away_sets


def _build_player_index(
    segments: list[dict],
    qualifying: set[int],
) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """Build player → segment index mappings.

    Returns:
        player_home_segs: player → set of segment indices where on home team
        player_away_segs: player → set of segment indices where on away team
    """
    player_home = defaultdict(set)
    player_away = defaultdict(set)

    for i, seg in enumerate(segments):
        for pid in seg["home_skater_ids"]:
            if pid in qualifying:
                player_home[pid].add(i)
        for pid in seg["away_skater_ids"]:
            if pid in qualifying:
                player_away[pid].add(i)

    return dict(player_home), dict(player_away)


def _compute_shared_toi(
    segments: list[dict],
    qualifying: set[int],
) -> dict[tuple[int, int], float]:
    """Compute shared same-team TOI for all qualifying pairs."""
    shared = defaultdict(float)
    for seg in segments:
        dur_min = seg["duration_seconds"] / 60.0
        home_q = [p for p in seg["home_skater_ids"] if p in qualifying]
        away_q = [p for p in seg["away_skater_ids"] if p in qualifying]
        for j in range(len(home_q)):
            for k in range(j + 1, len(home_q)):
                a, b = home_q[j], home_q[k]
                shared[(a, b)] += dur_min
                shared[(b, a)] += dur_min
        for j in range(len(away_q)):
            for k in range(j + 1, len(away_q)):
                a, b = away_q[j], away_q[k]
                shared[(a, b)] += dur_min
                shared[(b, a)] += dur_min
    return dict(shared)


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    total_w = weights.sum()
    if total_w <= 0:
        return 0.0
    return float(np.dot(values, weights) / total_w)


def compute_elevation(
    segments: list[dict],
    ratings_off: dict[int, float],
    qualifying_player_ids: list[int],
    min_shared_minutes: float = MIN_SHARED_MINUTES,
    min_apart_minutes: float = MIN_APART_MINUTES,
    min_qualifying_pairs: int = MIN_QUALIFYING_PAIRS,
) -> dict[int, PlayerElevation]:
    """Compute WOWY elevation scores for all qualifying players.

    Args:
        segments: 5v5 shift segments (from load_segments).
        ratings_off: player_id → offensive RAPM rating.
        qualifying_player_ids: players with enough TOI for RAPM.
        min_shared_minutes: min same-team TOI for a pair to qualify.
        min_apart_minutes: min TOI of B without A for pair to qualify.
        min_qualifying_pairs: min teammate pairs for a player's aggregate.

    Returns:
        dict mapping player_id → PlayerElevation for players with enough
        qualifying pairs.
    """
    qualifying = set(qualifying_player_ids)
    logger.info("Computing elevation for %d qualifying players...", len(qualifying))

    logger.info("Building segment arrays and player index...")
    durations, xgf60, home_sets, away_sets = _build_segment_arrays(
        segments, qualifying
    )
    player_home, player_away = _build_player_index(segments, qualifying)

    logger.info("Computing shared TOI for all pairs...")
    shared_toi = _compute_shared_toi(segments, qualifying)

    player_total_segs = {}
    for pid in qualifying:
        player_total_segs[pid] = (
            player_home.get(pid, set()) | player_away.get(pid, set())
        )

    player_total_toi = {}
    for pid in qualifying:
        player_total_toi[pid] = float(durations[list(player_total_segs[pid])].sum())

    n_pairs_checked = 0
    n_pairs_qualified = 0
    pair_results: dict[int, list[PairElevation]] = defaultdict(list)

    for pid_a in qualifying:
        a_segs = player_total_segs[pid_a]
        teammates = set()
        for key in shared_toi:
            if key[0] == pid_a and key[1] in qualifying:
                teammates.add(key[1])

        for pid_b in teammates:
            stoi = shared_toi.get((pid_a, pid_b), 0.0)
            if stoi < min_shared_minutes:
                continue

            b_segs = player_total_segs[pid_b]
            without_a_segs = b_segs - a_segs
            apart_toi = float(durations[list(without_a_segs)].sum()) if without_a_segs else 0.0
            if apart_toi < min_apart_minutes:
                continue

            n_pairs_checked += 1

            # "With A" segments: both on same team
            with_home = player_home.get(pid_a, set()) & player_home.get(pid_b, set())
            with_away = player_away.get(pid_a, set()) & player_away.get(pid_b, set())
            with_segs = sorted(with_home | with_away)

            without_segs = sorted(without_a_segs)

            if not with_segs or not without_segs:
                continue

            with_idx = np.array(with_segs)
            without_idx = np.array(without_segs)

            with_dur = durations[with_idx]
            without_dur = durations[without_idx]

            # Actual xGF/60 from B's team perspective
            with_actual_vals = np.zeros(len(with_idx))
            with_pred_vals = np.zeros(len(with_idx))
            for j, si in enumerate(with_segs):
                if pid_b in home_sets[si]:
                    with_actual_vals[j] = xgf60[si, 0]
                    with_pred_vals[j] = sum(
                        ratings_off.get(p, 0.0) for p in home_sets[si]
                    )
                else:
                    with_actual_vals[j] = xgf60[si, 1]
                    with_pred_vals[j] = sum(
                        ratings_off.get(p, 0.0) for p in away_sets[si]
                    )

            without_actual_vals = np.zeros(len(without_idx))
            without_pred_vals = np.zeros(len(without_idx))
            for j, si in enumerate(without_segs):
                if pid_b in home_sets[si]:
                    without_actual_vals[j] = xgf60[si, 0]
                    without_pred_vals[j] = sum(
                        ratings_off.get(p, 0.0) for p in home_sets[si]
                    )
                else:
                    without_actual_vals[j] = xgf60[si, 1]
                    without_pred_vals[j] = sum(
                        ratings_off.get(p, 0.0) for p in away_sets[si]
                    )

            with_actual = _weighted_mean(with_actual_vals, with_dur)
            with_pred = _weighted_mean(with_pred_vals, with_dur)
            without_actual = _weighted_mean(without_actual_vals, without_dur)
            without_pred = _weighted_mean(without_pred_vals, without_dur)

            actual_gap = with_actual - without_actual
            pred_gap = with_pred - without_pred
            residual = actual_gap - pred_gap

            pair_results[pid_a].append(PairElevation(
                player_a=pid_a,
                player_b=pid_b,
                shared_toi=stoi,
                apart_toi=apart_toi,
                with_actual=with_actual,
                with_predicted=with_pred,
                without_actual=without_actual,
                without_predicted=without_pred,
                residual=residual,
            ))
            n_pairs_qualified += 1

    logger.info(
        "Checked %d pairs, %d qualified (>= %.0f min shared, >= %.0f min apart)",
        n_pairs_checked, n_pairs_qualified, min_shared_minutes, min_apart_minutes,
    )

    results = {}
    for pid_a in qualifying:
        pairs = pair_results.get(pid_a, [])
        if len(pairs) < min_qualifying_pairs:
            continue

        weights = np.array([p.shared_toi for p in pairs])
        residuals = np.array([p.residual for p in pairs])
        elevation_score = _weighted_mean(residuals, weights)

        results[pid_a] = PlayerElevation(
            player_id=pid_a,
            elevation_score=elevation_score,
            n_pairs=len(pairs),
            pairs=pairs,
        )

    logger.info(
        "%d players with >= %d qualifying pairs → elevation scores computed",
        len(results), min_qualifying_pairs,
    )

    return results

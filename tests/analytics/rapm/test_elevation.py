"""Tests for the WOWY elevation metric."""

import numpy as np
import pytest

from src.analytics.rapm.elevation import (
    _build_segment_arrays,
    _build_player_index,
    _compute_shared_toi,
    _weighted_mean,
    compute_elevation,
    PairElevation,
)


def _seg(game_id, home, away, duration, home_xgf, away_xgf):
    return {
        "game_id": game_id,
        "period": 1,
        "duration_seconds": duration,
        "home_skater_ids": home,
        "away_skater_ids": away,
        "home_xgf": home_xgf,
        "away_xgf": away_xgf,
        "score_state": 0,
    }


class TestBuildSegmentArrays:
    def test_shapes(self):
        segments = [
            _seg(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.05),
            _seg(2, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 120, 0.2, 0.1),
        ]
        qualifying = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
        dur, xgf, h_sets, a_sets = _build_segment_arrays(segments, qualifying)
        assert dur.shape == (2,)
        assert xgf.shape == (2, 2)
        assert len(h_sets) == 2
        assert len(a_sets) == 2

    def test_xgf60_conversion(self):
        segments = [_seg(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.05)]
        qualifying = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
        dur, xgf, _, _ = _build_segment_arrays(segments, qualifying)
        assert abs(dur[0] - 1.0) < 0.001  # 60s = 1 min
        assert abs(xgf[0, 0] - 6.0) < 0.01  # 0.1/1min * 60 = 6.0
        assert abs(xgf[0, 1] - 3.0) < 0.01

    def test_filters_non_qualifying(self):
        segments = [_seg(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.05)]
        qualifying = {1, 2, 3}
        _, _, h_sets, a_sets = _build_segment_arrays(segments, qualifying)
        assert h_sets[0] == {1, 2, 3}
        assert a_sets[0] == set()


class TestBuildPlayerIndex:
    def test_basic(self):
        segments = [
            _seg(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.05),
            _seg(2, [1, 2, 3, 4, 11], [6, 7, 8, 9, 10], 60, 0.1, 0.05),
        ]
        qualifying = {1, 2, 6, 11}
        home, away = _build_player_index(segments, qualifying)
        assert home[1] == {0, 1}
        assert home[11] == {1}
        assert away[6] == {0, 1}
        assert 5 not in home


class TestComputeSharedToi:
    def test_symmetric(self):
        segments = [
            _seg(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 120, 0.1, 0.05),
        ]
        qualifying = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
        shared = _compute_shared_toi(segments, qualifying)
        assert abs(shared[(1, 2)] - 2.0) < 0.01  # 120s = 2 min
        assert abs(shared[(2, 1)] - 2.0) < 0.01

    def test_across_segments(self):
        segments = [
            _seg(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.05),
            _seg(2, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.05),
        ]
        qualifying = {1, 2}
        shared = _compute_shared_toi(segments, qualifying)
        assert abs(shared[(1, 2)] - 2.0) < 0.01  # 1 + 1 = 2 min

    def test_opponents_not_shared(self):
        segments = [
            _seg(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.05),
        ]
        qualifying = {1, 6}
        shared = _compute_shared_toi(segments, qualifying)
        assert (1, 6) not in shared


class TestWeightedMean:
    def test_basic(self):
        result = _weighted_mean(np.array([2.0, 4.0]), np.array([1.0, 1.0]))
        assert abs(result - 3.0) < 0.001

    def test_weighted(self):
        result = _weighted_mean(np.array([2.0, 4.0]), np.array([3.0, 1.0]))
        assert abs(result - 2.5) < 0.001

    def test_zero_weights(self):
        result = _weighted_mean(np.array([2.0, 4.0]), np.array([0.0, 0.0]))
        assert result == 0.0


class TestComputeElevation:
    def _make_synthetic_segments(self):
        """Build segments where Player A (id=1) elevates teammates.

        When Player A is on ice, the team generates 3.0 xGF/60.
        RAPM says the team should generate 2.5 xGF/60 based on personnel.
        The 0.5 gap is the elevation signal — Player A makes teammates
        produce more than their individual ratings predict.

        When Player A is off ice, B plays with Player 11 (same RAPM rating
        as A), and the team generates exactly the RAPM-predicted 2.5 xGF/60.
        """
        rng = np.random.RandomState(99)
        segments = []
        n_segments = 4000

        base_rate = 2.5 / 3600  # per-second
        bonus_rate = 0.5 / 3600  # per-second elevation by Player A

        player_pool_b = [2, 3, 4, 5]
        player_pool_away = [6, 7, 8, 9, 10]

        for i in range(n_segments):
            duration = 40
            config = i % 4

            if config == 0:
                # A + some of pool_b on home, away fixed
                home = [1] + player_pool_b
                away = player_pool_away
                home_xgf = (base_rate + bonus_rate) * duration
            elif config == 1:
                # A off, Player 11 replaces A on home
                home = [11] + player_pool_b
                away = player_pool_away
                home_xgf = base_rate * duration
            elif config == 2:
                # A + some of pool_b on away
                home = player_pool_away
                away = [1] + player_pool_b
                away_xgf_val = (base_rate + bonus_rate) * duration
                home_xgf_val = base_rate * duration
                home_xgf = home_xgf_val
                segments.append(_seg(
                    i, home, away, duration,
                    home_xgf + rng.normal(0, 0.0005),
                    away_xgf_val + rng.normal(0, 0.0005),
                ))
                continue
            else:
                # A off, Player 11 replaces A on away
                home = player_pool_away
                away = [11] + player_pool_b
                home_xgf_val = base_rate * duration
                away_xgf_val = base_rate * duration
                segments.append(_seg(
                    i, home, away, duration,
                    home_xgf_val + rng.normal(0, 0.0005),
                    away_xgf_val + rng.normal(0, 0.0005),
                ))
                continue

            away_xgf = base_rate * duration
            segments.append(_seg(
                i, home, away, duration,
                home_xgf + rng.normal(0, 0.0005),
                away_xgf + rng.normal(0, 0.0005),
            ))

        # RAPM ratings: all players are equal at 0.5 xGF/60
        ratings = {pid: 0.5 for pid in range(1, 12)}
        qualifying = list(range(1, 12))
        return segments, ratings, qualifying

    def test_elevator_has_positive_score(self):
        segments, ratings, qualifying = self._make_synthetic_segments()
        results = compute_elevation(
            segments, ratings, qualifying,
            min_shared_minutes=0, min_apart_minutes=0, min_qualifying_pairs=1,
        )
        assert 1 in results
        assert results[1].elevation_score > 0

    def test_non_elevator_negative(self):
        """Player 11 always gets replaced by elevator A, so 11's elevation is negative."""
        segments, ratings, qualifying = self._make_synthetic_segments()
        results = compute_elevation(
            segments, ratings, qualifying,
            min_shared_minutes=0, min_apart_minutes=0, min_qualifying_pairs=1,
        )
        if 11 in results:
            assert results[11].elevation_score < 0
            assert results[1].elevation_score > 0

    def test_elevator_higher_than_replacement(self):
        segments, ratings, qualifying = self._make_synthetic_segments()
        results = compute_elevation(
            segments, ratings, qualifying,
            min_shared_minutes=0, min_apart_minutes=0, min_qualifying_pairs=1,
        )
        if 11 in results:
            assert results[1].elevation_score > results[11].elevation_score

    def test_min_qualifying_pairs_filter(self):
        segments, ratings, qualifying = self._make_synthetic_segments()
        # Players 2-5 have fixed linemates, so each has 4 qualifying pairs
        # Requiring 10 should filter everyone out
        results = compute_elevation(
            segments, ratings, qualifying,
            min_shared_minutes=0, min_apart_minutes=0, min_qualifying_pairs=10,
        )
        assert len(results) == 0

    def test_min_shared_toi_filter(self):
        segments, ratings, qualifying = self._make_synthetic_segments()
        # Each player has ~4000 * 40/60 / 4 ≈ 667 min with linemates
        # Setting threshold above that should filter everyone
        results = compute_elevation(
            segments, ratings, qualifying,
            min_shared_minutes=10000, min_apart_minutes=0, min_qualifying_pairs=1,
        )
        assert len(results) == 0

    def test_pair_details_populated(self):
        segments, ratings, qualifying = self._make_synthetic_segments()
        results = compute_elevation(
            segments, ratings, qualifying,
            min_shared_minutes=0, min_apart_minutes=0, min_qualifying_pairs=1,
        )
        assert 1 in results
        assert results[1].n_pairs > 0
        assert len(results[1].pairs) == results[1].n_pairs
        for pair in results[1].pairs:
            assert pair.player_a == 1
            assert pair.shared_toi > 0

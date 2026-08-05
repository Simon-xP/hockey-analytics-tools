"""Tests for the RAPM ridge regression model."""

import numpy as np
import pytest
from scipy import sparse

from src.analytics.rapm.model import (
    build_design_matrix,
    _compute_percentiles,
    RAPMResult,
)


def _make_segment(
    game_id, home_ids, away_ids, duration, home_xgf, away_xgf, score=0
):
    return {
        "game_id": game_id,
        "period": 1,
        "duration_seconds": duration,
        "home_skater_ids": home_ids,
        "away_skater_ids": away_ids,
        "home_xgf": home_xgf,
        "away_xgf": away_xgf,
        "score_state": score,
    }


class TestBuildDesignMatrix:
    def test_basic_shape(self):
        segments = [
            _make_segment(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.05),
            _make_segment(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.0, 0.1),
        ]
        X, y_off, y_def, w, pids, toi, gids = build_design_matrix(
            segments, min_toi_minutes=0
        )
        assert X.shape[0] == 4  # 2 segments * 2 rows each
        assert X.shape[1] == 11  # 10 players + is_home
        assert len(pids) == 10

    def test_one_sided_encoding(self):
        """Each row should have exactly 5 player indicators + 0 or 1 is_home."""
        segments = [
            _make_segment(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 120, 0.1, 0.05),
        ]
        X, y_off, y_def, w, pids, toi, gids = build_design_matrix(
            segments, min_toi_minutes=0
        )
        # Home row: 5 player indicators + 1 is_home = 6 non-zeros
        assert X[0].nnz == 6
        # Away row: 5 player indicators + 0 is_home = 5 non-zeros
        assert X[1].nnz == 5

    def test_response_values(self):
        segments = [
            _make_segment(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.05),
        ]
        X, y_off, y_def, w, pids, toi, gids = build_design_matrix(
            segments, min_toi_minutes=0
        )
        # Home row: off = home xGF/60, def = away xGF/60 (what opponent generated)
        assert abs(y_off[0] - 6.0) < 0.01  # 0.1 / (60/3600) * ... = 0.1/1min * 60 = 6.0
        assert abs(y_def[0] - 3.0) < 0.01  # 0.05/1min * 60 = 3.0
        # Away row: off = away xGF/60, def = home xGF/60
        assert abs(y_off[1] - 3.0) < 0.01
        assert abs(y_def[1] - 6.0) < 0.01

    def test_min_toi_filter(self):
        segments = [
            _make_segment(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.05),
        ]
        X, y_off, y_def, w, pids, toi, gids = build_design_matrix(
            segments, min_toi_minutes=10
        )
        assert len(pids) == 0  # Nobody has 10 minutes from 1 minute of play

    def test_weights_are_duration(self):
        segments = [
            _make_segment(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 120, 0.1, 0.05),
        ]
        X, y_off, y_def, w, pids, toi, gids = build_design_matrix(
            segments, min_toi_minutes=0
        )
        assert abs(w[0] - 2.0) < 0.01  # 120s = 2 min
        assert abs(w[1] - 2.0) < 0.01


class TestSyntheticRAPM:
    """Verify RAPM recovers known player effects from synthetic data."""

    def test_recovers_offensive_player(self):
        """Player A adds 0.5 xGF/60 whenever they're on ice."""
        from sklearn.linear_model import Ridge

        rng = np.random.RandomState(42)
        n_segments = 8000
        player_pool = list(range(2, 31))
        segments = []

        for i in range(n_segments):
            rng.shuffle(player_pool)
            config = i % 4
            if config == 0:
                home = [1] + player_pool[:4]
                away = player_pool[4:9]
            elif config == 1:
                home = player_pool[:5]
                away = [1] + player_pool[5:9]
            else:
                home = player_pool[:5]
                away = player_pool[5:10]

            base_rate = 2.5 / 3600
            duration = 40
            bonus = (0.5 / 3600) * duration

            home_xgf = base_rate * duration + rng.normal(0, 0.001)
            away_xgf = base_rate * duration + rng.normal(0, 0.001)
            if config == 0:
                home_xgf += bonus
            elif config == 1:
                away_xgf += bonus

            segments.append(_make_segment(
                i, home, away, duration,
                max(0, home_xgf), max(0, away_xgf),
            ))

        X, y_off, y_def, w, pids, toi, gids = build_design_matrix(
            segments, min_toi_minutes=0
        )

        model = Ridge(alpha=0.01, fit_intercept=False)
        model.fit(X, y_off, sample_weight=w)

        n_players = len(pids)
        ratings = model.coef_[:n_players]
        player_a_idx = pids.index(1)

        other_ratings = [ratings[j] for j in range(n_players) if j != player_a_idx]
        assert ratings[player_a_idx] > max(other_ratings)

    def test_high_lambda_shrinks_to_zero(self):
        from sklearn.linear_model import Ridge

        segments = []
        for i in range(100):
            segments.append(_make_segment(
                i, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.1,
            ))

        X, y_off, y_def, w, pids, toi, gids = build_design_matrix(
            segments, min_toi_minutes=0
        )

        model = Ridge(alpha=1e6, fit_intercept=False)
        model.fit(X, y_off, sample_weight=w)

        n_players = len(pids)
        assert all(abs(c) < 0.01 for c in model.coef_[:n_players])


class TestComputePercentiles:
    def test_basic(self):
        values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        pcts = _compute_percentiles(values)
        assert pcts[0] == 20  # lowest
        assert pcts[4] == 100  # highest

    def test_single_value(self):
        pcts = _compute_percentiles(np.array([5.0]))
        assert pcts[0] == 100


@pytest.mark.integration
class TestRAPMIntegration:
    def test_fit_on_real_data(self):
        from src.analytics.rapm.model import fit_rapm
        from src.core.db import get_session

        with get_session() as session:
            result = fit_rapm(
                session,
                season_start='2025-10-01',
                season_end='2026-07-01',
                run_defensive=False,
                cv_lambdas=[10, 100],
            )

            assert result.n_players > 400
            assert result.n_segments > 100000
            assert result.home_ice_coef_off > 0
            assert abs(np.mean(result.ratings_off)) < 0.5

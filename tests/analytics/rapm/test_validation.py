"""Tests for RAPM validation functions."""

import numpy as np
import pytest

from src.analytics.rapm.validation import split_half_reliability, predictive_power


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


class TestSplitHalfReliability:
    def _make_consistent_segments(self, n_games=200):
        """Segments where Player 1 consistently adds xGF."""
        rng = np.random.RandomState(42)
        pool = list(range(2, 31))
        segments = []

        for g in range(n_games):
            rng.shuffle(pool)
            for shift in range(20):
                if shift % 2 == 0:
                    home = [1] + pool[:4]
                    away = pool[4:9]
                    bonus = 0.003
                else:
                    home = pool[:5]
                    away = pool[5:10]
                    bonus = 0

                base = 2.5 / 3600
                dur = 30
                h_xgf = base * dur + bonus + rng.normal(0, 0.001)
                a_xgf = base * dur + rng.normal(0, 0.001)
                segments.append(_seg(g, home, away, dur, max(0, h_xgf), max(0, a_xgf)))

        return segments

    def test_positive_correlation(self):
        segments = self._make_consistent_segments()
        result = split_half_reliability(segments, min_toi_minutes=0)
        assert result.correlation > 0
        assert result.n_players > 0

    def test_too_few_players_raises(self):
        segments = [
            _seg(0, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.05),
            _seg(1, [1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60, 0.1, 0.05),
        ]
        with pytest.raises(ValueError, match="not enough"):
            split_half_reliability(segments, min_toi_minutes=1000)


class TestPredictivePower:
    def test_rapm_beats_baseline_on_consistent_data(self):
        """With a known strong signal, RAPM prediction should beat league average."""
        rng = np.random.RandomState(42)
        pool = list(range(2, 31))

        def make_segments(n_games):
            segs = []
            for g in range(n_games):
                rng.shuffle(pool)
                for shift in range(20):
                    if shift % 2 == 0:
                        home = [1] + pool[:4]
                        away = pool[4:9]
                        bonus = 0.003
                    else:
                        home = pool[:5]
                        away = pool[5:10]
                        bonus = 0
                    base = 2.5 / 3600
                    dur = 30
                    h_xgf = base * dur + bonus + rng.normal(0, 0.001)
                    a_xgf = base * dur + rng.normal(0, 0.001)
                    segs.append(_seg(g, home, away, dur, max(0, h_xgf), max(0, a_xgf)))
            return segs

        train = make_segments(200)
        test = make_segments(200)
        # Offset game IDs so they don't overlap
        for s in test:
            s["game_id"] += 1000

        result = predictive_power(train, test, min_toi_minutes=0)
        assert result.correlation > 0.3
        assert result.n_players > 0

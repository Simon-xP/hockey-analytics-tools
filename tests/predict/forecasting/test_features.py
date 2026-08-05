"""Tests for v2 forecasting feature extractors (pure functions, no DB)."""

import math

import numpy as np
import pytest

from src.predict.forecasting.features import (
    ewma,
    safe_per_60,
    safe_ratio,
    extract_rolling_features,
    extract_blended_features,
    extract_ipp_features,
    extract_position_features,
)
from src.predict.forecasting.constants import (
    IPP_POSITION_MEANS,
    IPP_DEFAULT_MEAN,
    IPP_STABILIZATION_K,
    PRIOR_SEASON_BLEND_K,
)


def _make_game(**overrides) -> dict:
    """Create a synthetic game dict with sensible defaults."""
    game = {
        "game_id": 2025020001,
        "game_date": None,
        "toi_seconds": 900,
        "goals": 0, "assists": 0, "first_assists": 0, "second_assists": 0,
        "points": 0, "shots": 3, "shot_attempts": 5, "missed_shots": 1,
        "blocked_shots": 1, "hits": 2, "blocks": 1, "giveaways": 0,
        "takeaways": 0, "penalties": 0, "penalties_drawn": 0,
        "faceoff_wins": 3, "faceoff_losses": 2,
        "ixg": 0.2, "cf": 15, "ca": 12, "ff": 12, "fa": 10,
        "sf": 8, "sa": 7, "gf": 2, "ga": 1,
        "xgf": 1.2, "xga": 0.8, "scf": 6, "sca": 4,
        "hdcf": 3, "hdca": 2,
        "oz_starts": 4, "dz_starts": 3, "nz_starts": 2,
        "ipp": 0.5,
    }
    game.update(overrides)
    return game


# =============================================================================
# Utility functions
# =============================================================================


class TestEWMA:
    def test_empty_returns_nan(self):
        assert math.isnan(ewma([], 5))

    def test_single_value(self):
        assert ewma([3.0], 5) == 3.0

    def test_recent_weighted_more(self):
        result = ewma([10.0, 0.0, 0.0, 0.0, 0.0], 2)
        simple_avg = 2.0
        assert result > simple_avg

    def test_all_same(self):
        result = ewma([2.0, 2.0, 2.0], 5)
        assert abs(result - 2.0) < 1e-6


class TestSafePer60:
    def test_normal(self):
        assert abs(safe_per_60(2, 1200) - 6.0) < 1e-6

    def test_zero_toi(self):
        assert math.isnan(safe_per_60(1, 0))

    def test_negative_toi(self):
        assert math.isnan(safe_per_60(1, -100))

    def test_zero_count(self):
        assert safe_per_60(0, 900) == 0.0


class TestSafeRatio:
    def test_normal(self):
        assert abs(safe_ratio(60, 40) - 0.6) < 1e-6

    def test_zero_denominator(self):
        assert math.isnan(safe_ratio(0, 0))

    def test_all_numerator(self):
        assert abs(safe_ratio(10, 0) - 1.0) < 1e-6


# =============================================================================
# extract_rolling_features
# =============================================================================


class TestExtractRollingFeatures:
    def test_empty_games(self):
        result = extract_rolling_features([])
        assert result == {"season_gp": 0.0}

    def test_season_gp_equals_len(self):
        games = [_make_game() for _ in range(12)]
        result = extract_rolling_features(games)
        assert result["season_gp"] == 12.0

    def test_l5_window_uses_first_5_games(self):
        games = []
        for i in range(10):
            games.append(_make_game(goals=1 if i < 5 else 0, toi_seconds=3600))
        result = extract_rolling_features(games)
        assert abs(result["l5_goals"] - 1.0) < 1e-6

    def test_l6_15_window_uses_games_5_to_14(self):
        games = []
        for i in range(20):
            goals = 2 if 5 <= i < 15 else 0
            games.append(_make_game(goals=goals, toi_seconds=3600))
        result = extract_rolling_features(games)
        assert abs(result["l6_15_goals"] - 2.0) < 1e-6

    def test_per_60_conversion(self):
        games = [_make_game(goals=1, toi_seconds=1200)]
        result = extract_rolling_features(games)
        expected = 1 / 1200 * 3600  # 3.0 goals per 60
        assert abs(result["l5_goals"] - expected) < 1e-6

    def test_season_avg_is_mean(self):
        games = [
            _make_game(goals=2, toi_seconds=3600),
            _make_game(goals=0, toi_seconds=3600),
        ]
        result = extract_rolling_features(games)
        assert abs(result["season_avg_goals"] - 1.0) < 1e-6

    def test_season_avg_toi(self):
        games = [
            _make_game(toi_seconds=900),
            _make_game(toi_seconds=1100),
        ]
        result = extract_rolling_features(games)
        assert abs(result["season_avg_toi"] - 1000.0) < 1e-6

    def test_sh_pct_nan_when_no_shots(self):
        games = [_make_game(goals=0, shots=0)]
        result = extract_rolling_features(games)
        assert "l5_sh_pct" not in result

    def test_sh_pct_calculated_correctly(self):
        games = [_make_game(goals=1, shots=4)]
        result = extract_rolling_features(games)
        assert abs(result["l5_sh_pct"] - 0.25) < 1e-6

    def test_ratio_stats_computed(self):
        games = [_make_game(cf=60, ca=40)]
        result = extract_rolling_features(games)
        assert abs(result["l5_cf_pct"] - 0.6) < 1e-6

    def test_on_ice_rate_stats_per_60(self):
        games = [_make_game(cf=10, toi_seconds=600)]
        result = extract_rolling_features(games)
        expected = 10 / 600 * 3600  # 60.0
        assert abs(result["l5_oi_cf"] - expected) < 1e-6


# =============================================================================
# extract_blended_features
# =============================================================================


class TestExtractBlendedFeatures:
    def test_zero_gp_returns_100_pct_prior(self):
        rolling = {"season_gp": 0}
        prior = {"prior_goals": 2.0}
        result = extract_blended_features(rolling, prior)
        assert abs(result["blended_goals"] - 2.0) < 1e-6

    def test_at_k_gp_returns_50_50(self):
        k = PRIOR_SEASON_BLEND_K
        rolling = {"season_gp": k, "season_avg_goals": 4.0}
        prior = {"prior_goals": 2.0}
        result = extract_blended_features(rolling, prior, k=k)
        expected = (2.0 * k + 4.0 * k) / (k + k)
        assert abs(result["blended_goals"] - expected) < 1e-6

    def test_missing_prior_returns_current_only(self):
        rolling = {"season_gp": 20, "season_avg_goals": 3.0}
        prior = {}
        result = extract_blended_features(rolling, prior)
        assert abs(result["blended_goals"] - 3.0) < 1e-6

    def test_missing_current_returns_prior_only(self):
        rolling = {"season_gp": 0}
        prior = {"prior_goals": 5.0}
        result = extract_blended_features(rolling, prior)
        assert abs(result["blended_goals"] - 5.0) < 1e-6

    def test_both_missing_returns_empty(self):
        rolling = {"season_gp": 0}
        prior = {}
        result = extract_blended_features(rolling, prior)
        assert result == {}

    def test_nan_prior_skips_blend(self):
        rolling = {"season_gp": 10, "season_avg_goals": 2.0}
        prior = {"prior_goals": float("nan")}
        result = extract_blended_features(rolling, prior)
        # When both keys exist but prior is NaN, the outer if matches
        # but inner finite check fails — no blended feature produced.
        assert "blended_goals" not in result

    def test_all_stats_blended(self):
        stats = [
            "goals", "assists", "shots", "ixg", "shot_attempts",
            "hits", "blocks", "first_assists", "second_assists",
            "cf", "ca", "xgf", "xga", "hdcf",
        ]
        rolling = {"season_gp": 20}
        prior = {}
        for s in stats:
            rolling[f"season_avg_{s}"] = 1.0
            prior[f"prior_{s}"] = 2.0
        result = extract_blended_features(rolling, prior)
        for s in stats:
            assert f"blended_{s}" in result, f"blended_{s} missing"


# =============================================================================
# extract_ipp_features
# =============================================================================


class TestExtractIPPFeatures:
    def test_empty_games(self):
        result = extract_ipp_features([], "C")
        assert result == {}

    def test_regressed_toward_position_mean_forward(self):
        games = [_make_game(points=1, gf=2) for _ in range(20)]
        result = extract_ipp_features(games, "C")
        raw_ipp = 20 / 40  # 0.5
        expected = (20 * raw_ipp + IPP_STABILIZATION_K * IPP_POSITION_MEANS["C"]) / (
            20 + IPP_STABILIZATION_K
        )
        assert abs(result["ipp_regressed"] - expected) < 1e-6

    def test_regressed_toward_position_mean_defense(self):
        games = [_make_game(points=1, gf=4) for _ in range(10)]
        result = extract_ipp_features(games, "D")
        raw_ipp = 10 / 40  # 0.25
        expected = (10 * raw_ipp + IPP_STABILIZATION_K * IPP_POSITION_MEANS["D"]) / (
            10 + IPP_STABILIZATION_K
        )
        assert abs(result["ipp_regressed"] - expected) < 1e-6

    def test_forward_vs_defense_different_means(self):
        games = [_make_game(points=1, gf=3) for _ in range(30)]
        result_c = extract_ipp_features(games, "C")
        result_d = extract_ipp_features(games, "D")
        assert result_c["ipp_regressed"] != result_d["ipp_regressed"]

    def test_season_raw_ipp(self):
        games = [
            _make_game(points=2, gf=3),
            _make_game(points=1, gf=2),
        ]
        result = extract_ipp_features(games, "C")
        assert abs(result["ipp_season_raw"] - 3 / 5) < 1e-6

    def test_zero_gf_season_raw_nan(self):
        games = [_make_game(points=0, gf=0)]
        result = extract_ipp_features(games, "C")
        assert math.isnan(result["ipp_season_raw"])
        assert abs(result["ipp_regressed"] - IPP_POSITION_MEANS["C"]) < 1e-6

    def test_ewma_computed_for_games_with_gf(self):
        games = [
            _make_game(points=1, gf=2),
            _make_game(points=0, gf=0),
            _make_game(points=2, gf=4),
        ]
        result = extract_ipp_features(games, "C")
        assert "ipp_ewma_10" in result
        assert "ipp_ewma_15" in result

    def test_unknown_position_uses_default(self):
        games = [_make_game(points=0, gf=0)]
        result = extract_ipp_features(games, "G")
        assert abs(result["ipp_regressed"] - IPP_DEFAULT_MEAN) < 1e-6


# =============================================================================
# extract_position_features
# =============================================================================


class TestExtractPositionFeatures:
    def test_center(self):
        result = extract_position_features("C")
        assert result["is_forward"] == 1.0
        assert result["is_center"] == 1.0

    def test_winger(self):
        result = extract_position_features("L")
        assert result["is_forward"] == 1.0
        assert result["is_center"] == 0.0

    def test_defense(self):
        result = extract_position_features("D")
        assert result["is_forward"] == 0.0
        assert result["is_center"] == 0.0

"""Tests for v2 forecasting model: feature filtering and SituationModel."""

import math

import numpy as np
import pytest

from src.predict.forecasting.constants import (
    STAT_FEATURE_FILTERS,
    _UNIVERSAL_FEATURES,
    feature_allowed_for_stat,
)
from src.predict.forecasting.model import SituationModel


# =============================================================================
# feature_allowed_for_stat
# =============================================================================


class TestFeatureAllowedForStat:
    """Verify per-stat feature gating logic."""

    @pytest.mark.parametrize("stat", ["goals", "assists", "shots", "hits", "blocks"])
    def test_universal_features_allowed_for_all_stats(self, stat):
        universal_examples = [
            "opp_gaa", "opp_gaa_10", "opp_gfa", "opp_is_b2b",
            "is_home", "is_b2b", "days_rest",
            "is_forward", "is_center",
            "season_gp", "prior_gp",
            "l5_toi", "season_avg_toi", "prior_toi_per_gp",
        ]
        for feat in universal_examples:
            assert feature_allowed_for_stat(feat, stat), (
                f"Universal feature '{feat}' should be allowed for {stat}"
            )

    @pytest.mark.parametrize("stat", ["goals", "assists", "shots", "hits", "blocks"])
    def test_stat_allows_own_rolling_features(self, stat):
        for prefix in ["l5", "l6_15", "l16_30", "season_avg", "prior", "blended"]:
            feat = f"{prefix}_{stat}"
            assert feature_allowed_for_stat(feat, stat), (
                f"{stat} model should allow its own feature '{feat}'"
            )

    def test_goals_excludes_hits_and_blocks(self):
        assert not feature_allowed_for_stat("l5_hits", "goals")
        assert not feature_allowed_for_stat("season_avg_hits", "goals")
        assert not feature_allowed_for_stat("blended_hits", "goals")
        assert not feature_allowed_for_stat("l5_blocks", "goals")
        assert not feature_allowed_for_stat("season_avg_blocks", "goals")

    def test_hits_only_allows_hits(self):
        assert feature_allowed_for_stat("l5_hits", "hits")
        assert not feature_allowed_for_stat("l5_goals", "hits")
        assert not feature_allowed_for_stat("l5_assists", "hits")
        assert not feature_allowed_for_stat("l5_shots", "hits")
        assert not feature_allowed_for_stat("l5_blocks", "hits")
        assert not feature_allowed_for_stat("l5_ixg", "hits")

    def test_blocks_allows_on_ice_stats(self):
        assert feature_allowed_for_stat("l5_blocks", "blocks")
        assert feature_allowed_for_stat("season_avg_cf", "blocks")
        assert feature_allowed_for_stat("l5_xgf_pct", "blocks")
        assert not feature_allowed_for_stat("l5_goals", "blocks")
        assert not feature_allowed_for_stat("l5_hits", "blocks")

    def test_hits_cross_stat_for_assists_and_shots(self):
        assert feature_allowed_for_stat("l5_hits", "assists")
        assert feature_allowed_for_stat("l5_hits", "shots")
        assert not feature_allowed_for_stat("l5_hits", "goals")
        assert not feature_allowed_for_stat("l5_hits", "blocks")

    def test_ipp_only_for_goals_and_assists(self):
        assert feature_allowed_for_stat("ipp_regressed", "goals")
        assert feature_allowed_for_stat("ipp_season_raw", "assists")
        assert feature_allowed_for_stat("ipp_ewma_10", "goals")
        assert not feature_allowed_for_stat("ipp_regressed", "shots")
        assert not feature_allowed_for_stat("ipp_regressed", "hits")
        assert not feature_allowed_for_stat("ipp_regressed", "blocks")

    def test_sh_pct_only_for_goals(self):
        assert feature_allowed_for_stat("l5_sh_pct", "goals")
        assert feature_allowed_for_stat("season_avg_sh_pct", "goals")
        assert not feature_allowed_for_stat("l5_sh_pct", "assists")
        assert not feature_allowed_for_stat("l5_sh_pct", "shots")
        assert not feature_allowed_for_stat("l5_sh_pct", "hits")
        assert not feature_allowed_for_stat("l5_sh_pct", "blocks")

    def test_penalties_drawn_not_allowed_for_hits(self):
        assert not feature_allowed_for_stat("l5_penalties_drawn", "hits")
        assert not feature_allowed_for_stat("season_avg_penalties_drawn", "hits")

    def test_penalties_not_allowed_for_hits(self):
        assert not feature_allowed_for_stat("l5_penalties", "hits")
        assert not feature_allowed_for_stat("prior_penalties", "hits")

    def test_unknown_stat_allows_everything(self):
        assert feature_allowed_for_stat("l5_goals", "unknown_stat")
        assert feature_allowed_for_stat("l5_hits", "unknown_stat")

    def test_on_ice_stats_for_goals(self):
        for stat in ["cf", "ca", "xgf", "xga", "hdcf"]:
            assert feature_allowed_for_stat(f"l5_{stat}", "goals")
        assert feature_allowed_for_stat("l5_cf_pct", "goals")
        assert feature_allowed_for_stat("l5_xgf_pct", "goals")


# =============================================================================
# SituationModel._get_feature_columns
# =============================================================================


class TestGetFeatureColumns:
    def test_dict_format_returns_stat_columns(self):
        model = SituationModel(situation="5v5")
        model.feature_columns = {
            "goals": ["l5_goals", "season_avg_goals"],
            "assists": ["l5_assists", "season_avg_assists"],
        }
        assert model._get_feature_columns("goals") == ["l5_goals", "season_avg_goals"]
        assert model._get_feature_columns("assists") == ["l5_assists", "season_avg_assists"]

    def test_dict_format_unknown_stat_returns_empty(self):
        model = SituationModel(situation="5v5")
        model.feature_columns = {"goals": ["l5_goals"]}
        assert model._get_feature_columns("hits") == []

    def test_list_format_returns_full_list(self):
        model = SituationModel(situation="5v5")
        model.feature_columns = ["l5_goals", "l5_assists", "l5_shots"]
        assert model._get_feature_columns("goals") == ["l5_goals", "l5_assists", "l5_shots"]
        assert model._get_feature_columns("hits") == ["l5_goals", "l5_assists", "l5_shots"]


# =============================================================================
# SituationModel._feature_vector
# =============================================================================


class TestFeatureVector:
    def test_correct_length(self):
        model = SituationModel(situation="5v5")
        model.feature_columns = {"goals": ["a", "b", "c"]}
        features = {"a": 1.0, "b": 2.0, "c": 3.0}
        vec = model._feature_vector(features, stat="goals")
        assert vec.shape == (1, 3)
        np.testing.assert_array_almost_equal(vec[0], [1.0, 2.0, 3.0])

    def test_missing_features_become_nan(self):
        model = SituationModel(situation="5v5")
        model.feature_columns = {"goals": ["a", "b", "c"]}
        features = {"a": 1.0}
        vec = model._feature_vector(features, stat="goals")
        assert vec.shape == (1, 3)
        assert vec[0, 0] == 1.0
        assert np.isnan(vec[0, 1])
        assert np.isnan(vec[0, 2])

    def test_inf_becomes_nan(self):
        model = SituationModel(situation="5v5")
        model.feature_columns = {"goals": ["a", "b"]}
        features = {"a": float("inf"), "b": float("-inf")}
        vec = model._feature_vector(features, stat="goals")
        assert np.isnan(vec[0, 0])
        assert np.isnan(vec[0, 1])

    def test_no_stat_uses_raw_feature_columns(self):
        model = SituationModel(situation="5v5")
        model.feature_columns = ["x", "y"]
        features = {"x": 5.0, "y": 10.0}
        vec = model._feature_vector(features)
        assert vec.shape == (1, 2)
        np.testing.assert_array_almost_equal(vec[0], [5.0, 10.0])

    def test_empty_features(self):
        model = SituationModel(situation="5v5")
        model.feature_columns = {"goals": ["a", "b"]}
        vec = model._feature_vector({}, stat="goals")
        assert vec.shape == (1, 2)
        assert np.isnan(vec[0, 0])
        assert np.isnan(vec[0, 1])

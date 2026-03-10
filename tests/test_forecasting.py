"""Tests for the forecasting module."""

import math

from src.tools.forecasting.baselines import (
    FORECAST_STATS,
    SeasonAverageModel,
    WeightedBlendModel,
)
from src.tools.forecasting.evaluation import (
    STAT_COLUMN_MAP,
    calibration,
    mae,
    poisson_log_likelihood,
)
from src.tools.forecasting.features import (
    INDIVIDUAL_OTHER_STATS,
    INDIVIDUAL_RATE_STATS,
    ON_ICE_PCT_STATS,
    ON_ICE_RATE_STATS,
)
from src.tools.forecasting.models import EvaluationResult, FeatureSet, Prediction


# =============================================================================
# LOSS FUNCTIONS
# =============================================================================

class TestMAE:
    def test_empty(self):
        assert mae([]) == 0.0

    def test_perfect(self):
        assert mae([(1.0, 1), (2.0, 2)]) == 0.0

    def test_basic(self):
        assert mae([(0.5, 0), (0.5, 1)]) == 0.5

    def test_single(self):
        assert mae([(3.0, 1)]) == 2.0


class TestPoissonLogLikelihood:
    def test_empty(self):
        assert poisson_log_likelihood([]) == 0.0

    def test_single_zero_actual(self):
        # P(k=0 | lambda=0.5) = e^(-0.5) => log = -0.5
        ll = poisson_log_likelihood([(0.5, 0)])
        expected = 0 * math.log(0.5) - 0.5 - math.lgamma(1)
        assert abs(ll - expected) < 1e-6

    def test_single_one_actual(self):
        # P(k=1 | lambda=0.5) = 0.5 * e^(-0.5)
        ll = poisson_log_likelihood([(0.5, 1)])
        expected = 1 * math.log(0.5) - 0.5 - math.lgamma(2)
        assert abs(ll - expected) < 1e-6

    def test_zero_prediction_clamped(self):
        # Should not crash with lambda=0
        ll = poisson_log_likelihood([(0.0, 0)])
        assert math.isfinite(ll)

    def test_higher_lambda_better_for_higher_actual(self):
        # For k=3, lambda=3 should score better than lambda=0.5
        ll_good = poisson_log_likelihood([(3.0, 3)])
        ll_bad = poisson_log_likelihood([(0.5, 3)])
        assert ll_good > ll_bad


class TestCalibration:
    def test_empty(self):
        assert calibration([]) == {}

    def test_single_bucket(self):
        preds = [(0.1, 0), (0.2, 0), (0.3, 1)]
        result = calibration(preds, n_buckets=1)
        assert len(result) == 1

    def test_two_buckets(self):
        preds = [(0.1, 0), (0.2, 0), (0.8, 1), (0.9, 1)]
        result = calibration(preds, n_buckets=2)
        assert len(result) == 2


# =============================================================================
# FEATURE SET
# =============================================================================

class TestFeatureSet:
    def test_get(self):
        fs = FeatureSet(nhl_id=1, game_id=1, game_date=None,
                        features={"a": 1.0, "b": 2.0})
        assert fs.get("a") == 1.0
        assert fs.get("missing") is None

    def test_rate(self):
        fs = FeatureSet(nhl_id=1, game_id=1, game_date=None,
                        features={"rolling_5_goals_per_60": 1.8})
        assert fs.rate("rolling_5", "goals_per_60") == 1.8
        assert fs.rate("rolling_10", "goals_per_60") is None


# =============================================================================
# BASELINE MODELS
# =============================================================================

class TestSeasonAverageModel:
    def test_basic(self):
        model = SeasonAverageModel()
        fs = FeatureSet(nhl_id=1, game_id=1, game_date=None, features={
            "season_avg_goals_per_60": 1.8,
            "season_avg_assists_per_60": 2.5,
        })
        pred = model.predict(fs)
        assert abs(pred["goals_per_60"] - 1.8) < 1e-6
        assert abs(pred["assists_per_60"] - 2.5) < 1e-6

    def test_missing_features(self):
        model = SeasonAverageModel()
        fs = FeatureSet(nhl_id=1, game_id=1, game_date=None, features={})
        pred = model.predict(fs)
        assert pred == {}

    def test_partial_features(self):
        model = SeasonAverageModel()
        fs = FeatureSet(nhl_id=1, game_id=1, game_date=None, features={
            "season_avg_goals_per_60": 1.5,
        })
        pred = model.predict(fs)
        assert "goals_per_60" in pred
        assert "assists_per_60" not in pred


class TestWeightedBlendModel:
    def test_all_components(self):
        model = WeightedBlendModel(w_season=0.4, w_recent=0.4, w_prior=0.2)
        fs = FeatureSet(nhl_id=1, game_id=1, game_date=None, features={
            "season_avg_goals_per_60": 1.8,
            "rolling_5_goals_per_60": 2.1,
            "prior_season_goals_per_gp": 0.35,
        })
        pred = model.predict(fs)
        expected = (0.4 * 1.8 + 0.4 * 2.1 + 0.2 * 0.35) / 1.0
        assert abs(pred["goals_per_60"] - expected) < 1e-6

    def test_fallback_season_only(self):
        model = WeightedBlendModel()
        fs = FeatureSet(nhl_id=1, game_id=1, game_date=None, features={
            "season_avg_goals_per_60": 1.8,
        })
        pred = model.predict(fs)
        assert abs(pred["goals_per_60"] - 1.8) < 1e-6

    def test_fallback_two_components(self):
        model = WeightedBlendModel(w_season=0.4, w_recent=0.4, w_prior=0.2)
        fs = FeatureSet(nhl_id=1, game_id=1, game_date=None, features={
            "season_avg_goals_per_60": 2.0,
            "rolling_5_goals_per_60": 3.0,
        })
        pred = model.predict(fs)
        # Weights renormalize: 0.4 + 0.4 = 0.8
        expected = (0.4 * 2.0 + 0.4 * 3.0) / 0.8
        assert abs(pred["goals_per_60"] - expected) < 1e-6

    def test_custom_window(self):
        model = WeightedBlendModel(recent_window=5)
        fs = FeatureSet(nhl_id=1, game_id=1, game_date=None, features={
            "season_avg_goals_per_60": 1.0,
            "rolling_5_goals_per_60": 3.0,
        })
        pred = model.predict(fs)
        assert pred["goals_per_60"] > 1.0  # blend should pull toward rolling

    def test_empty_features(self):
        model = WeightedBlendModel()
        fs = FeatureSet(nhl_id=1, game_id=1, game_date=None, features={})
        pred = model.predict(fs)
        assert pred == {}


# =============================================================================
# COLUMN ALIGNMENT
# =============================================================================

class TestColumnAlignment:
    """Verify feature extractors, baselines, and evaluation all reference
    columns that actually exist in the DB models."""

    def test_individual_rate_stats_exist_in_model(self):
        from src.core.models.game_stats import GameIndividualStats
        cols = {c.name for c in GameIndividualStats.__table__.columns}
        for col in INDIVIDUAL_RATE_STATS:
            assert col in cols, f"{col} not in GameIndividualStats"

    def test_individual_other_stats_exist_in_model(self):
        from src.core.models.game_stats import GameIndividualStats
        cols = {c.name for c in GameIndividualStats.__table__.columns}
        for col in INDIVIDUAL_OTHER_STATS:
            assert col in cols, f"{col} not in GameIndividualStats"

    def test_on_ice_rate_stats_exist_in_model(self):
        from src.core.models.game_stats import GameOnIceStats
        cols = {c.name for c in GameOnIceStats.__table__.columns}
        for col in ON_ICE_RATE_STATS:
            assert col in cols, f"{col} not in GameOnIceStats"

    def test_on_ice_pct_stats_exist_in_model(self):
        from src.core.models.game_stats import GameOnIceStats
        cols = {c.name for c in GameOnIceStats.__table__.columns}
        for col in ON_ICE_PCT_STATS:
            assert col in cols, f"{col} not in GameOnIceStats"

    def test_eval_stat_columns_exist_in_model(self):
        from src.core.models.game_stats import GameIndividualStats
        cols = {c.name for c in GameIndividualStats.__table__.columns}
        for stat, col in STAT_COLUMN_MAP.items():
            assert col in cols, (
                f"Eval stat '{stat}' maps to '{col}' "
                f"which is not in GameIndividualStats"
            )

    def test_forecast_stats_have_both_suffixes(self):
        """Each forecast stat should have per-60 and per-GP suffixes."""
        for stat, (per_60, per_gp) in FORECAST_STATS.items():
            assert "per_60" in per_60, f"{stat}: per-60 suffix missing 'per_60'"
            assert "per_gp" in per_gp, f"{stat}: per-GP suffix missing 'per_gp'"


class TestEvaluationResult:
    def test_summary(self):
        result = EvaluationResult(
            model_name="test",
            stat="goals_per_60",
            n_predictions=100,
            mae=0.5,
            poisson_log_likelihood=-0.8,
            mean_predicted=1.5,
            mean_actual=1.6,
        )
        s = result.summary()
        assert "test" in s
        assert "goals_per_60" in s
        assert "0.5000" in s


class TestPrediction:
    def test_create(self):
        p = Prediction(
            nhl_id=8478402,
            game_id=2024020001,
            game_date=None,
            stat="goals_per_60",
            predicted=1.8,
            model_name="test",
        )
        assert p.stat == "goals_per_60"
        assert p.predicted == 1.8

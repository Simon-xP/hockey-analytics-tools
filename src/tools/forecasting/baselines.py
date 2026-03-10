"""Baseline forecast models.

Both models follow the ForecastModel protocol: predict(feature_set) -> dict[str, float]

All predictions are per-60 rates (matching the game-level data from NST rate=y).
The prior season features are per-GP rates from SeasonStats raw counts.
"""

from dataclasses import dataclass

from src.tools.forecasting.models import FeatureSet

# Stats we forecast: stat name -> (per-60 feature suffix, prior-season per-GP suffix)
# Game-level features use per-60 suffixes; prior season uses per-GP suffixes
FORECAST_STATS = {
    "goals_per_60": ("goals_per_60", "goals_per_gp"),
    "assists_per_60": ("assists_per_60", "assists_per_gp"),
    "shots_per_60": ("shots_per_60", "shots_per_gp"),
    "hits_per_60": ("hits_per_60", "hits_per_gp"),
    "blocked_per_60": ("blocked_per_60", "blocked_per_gp"),
}


@dataclass
class SeasonAverageModel:
    """Predict per-60 rates from season average of all prior games.

    The simplest baseline: just use the player's season average so far.
    """
    name: str = "season_average"

    def predict(self, features: FeatureSet) -> dict[str, float]:
        predictions = {}
        for stat, (per_60_suffix, _) in FORECAST_STATS.items():
            avg = features.get(f"season_avg_{per_60_suffix}")
            if avg is not None:
                predictions[stat] = avg
        return predictions


@dataclass
class WeightedBlendModel:
    """Blend of season average + recent rolling average + prior season rate.

    prediction = w_season * season_avg + w_recent * rolling_N + w_prior * prior_season

    Note: season avg and rolling are per-60 rates; prior season is per-GP.
    These aren't directly comparable units, but the blend still works as a
    weighted signal combination. Falls back to available components.
    """
    name: str = "weighted_blend"

    w_season: float = 0.4
    w_recent: float = 0.4
    w_prior: float = 0.2

    recent_window: int = 5

    def predict(self, features: FeatureSet) -> dict[str, float]:
        predictions = {}

        for stat, (per_60_suffix, per_gp_suffix) in FORECAST_STATS.items():
            components = []
            weights = []

            # Season average (per-60)
            season_avg = features.get(f"season_avg_{per_60_suffix}")
            if season_avg is not None:
                components.append(season_avg)
                weights.append(self.w_season)

            # Recent rolling average (per-60)
            rolling = features.get(
                f"rolling_{self.recent_window}_{per_60_suffix}"
            )
            if rolling is not None:
                components.append(rolling)
                weights.append(self.w_recent)

            # Prior season rate (per-GP — different unit, but useful signal)
            prior = features.get(f"prior_season_{per_gp_suffix}")
            if prior is not None:
                components.append(prior)
                weights.append(self.w_prior)

            if components:
                total_weight = sum(weights)
                predictions[stat] = sum(
                    c * w / total_weight
                    for c, w in zip(components, weights)
                )

        return predictions

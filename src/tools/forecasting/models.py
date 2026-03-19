"""Data models for the forecasting module."""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class FeatureSet:
    """Feature vector for a single prediction.

    Uses a flat dict for flexibility — feature extractors add keys freely,
    and models consume whatever keys they need.
    """
    nhl_id: int
    game_id: int | None
    game_date: date
    features: dict[str, float] = field(default_factory=dict)

    def get(self, key: str) -> Optional[float]:
        return self.features.get(key)

    def rate(self, prefix: str, stat: str) -> Optional[float]:
        """Get a rate feature like rolling_5_goals or season_avg_ixg."""
        return self.features.get(f"{prefix}_{stat}")


@dataclass
class Prediction:
    """A single stat prediction for one player in one game."""
    nhl_id: int
    game_id: int | None
    game_date: date
    stat: str  # "goals_per_60", "assists_per_60", "shots_per_60", etc.
    predicted: float
    model_name: str


@dataclass
class EvaluationResult:
    """Aggregate evaluation metrics for a model on one stat."""
    model_name: str
    stat: str
    n_predictions: int
    mae: float
    poisson_log_likelihood: float
    mean_predicted: float
    mean_actual: float
    calibration_buckets: dict[str, tuple[float, float]] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"{self.model_name} | {self.stat}: "
            f"MAE={self.mae:.4f}, PoissonLL={self.poisson_log_likelihood:.4f}, "
            f"n={self.n_predictions}, "
            f"mean_pred={self.mean_predicted:.4f}, mean_actual={self.mean_actual:.4f}"
        )


@dataclass
class BacktestResult:
    """Full backtest result including all individual predictions."""
    model_name: str
    stat: str
    season: str
    overall: EvaluationResult
    predictions: list[tuple[Prediction, float]] = field(default_factory=list)
    # Each entry is (prediction, actual_per_60_value)

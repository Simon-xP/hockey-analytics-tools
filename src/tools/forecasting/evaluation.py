"""Evaluation metrics and backtest harness for forecasting models."""

import math
from collections import defaultdict
from typing import Protocol

from sqlalchemy.orm import Session

from src.core.db import get_session
from src.core.models import GameIndividualStats, Player
from src.tools.forecasting.models import (
    BacktestResult,
    EvaluationResult,
    FeatureSet,
    Prediction,
)

# =============================================================================
# LOSS FUNCTIONS
# =============================================================================

def mae(predictions: list[tuple[float, int]]) -> float:
    """Mean absolute error. predictions is list of (predicted, actual)."""
    if not predictions:
        return 0.0
    return sum(abs(p - a) for p, a in predictions) / len(predictions)


def poisson_log_likelihood(predictions: list[tuple[float, int]]) -> float:
    """Average Poisson log-likelihood.

    log P(k|lambda) = k*log(lambda) - lambda - log(k!)

    Higher is better. Returns average across all predictions.
    Clamps lambda to avoid log(0).
    """
    if not predictions:
        return 0.0

    total = 0.0
    for predicted, actual in predictions:
        lam = max(predicted, 1e-6)  # avoid log(0)
        ll = actual * math.log(lam) - lam - math.lgamma(actual + 1)
        total += ll

    return total / len(predictions)


def calibration(
    predictions: list[tuple[float, int]],
    n_buckets: int = 5,
) -> dict[str, tuple[float, float]]:
    """Compute calibration: group predictions into buckets, compare mean pred vs mean actual.

    Returns dict mapping bucket label -> (mean_predicted, mean_actual).
    """
    if not predictions:
        return {}

    sorted_preds = sorted(predictions, key=lambda x: x[0])
    bucket_size = max(1, len(sorted_preds) // n_buckets)
    buckets = {}

    for i in range(n_buckets):
        start = i * bucket_size
        end = start + bucket_size if i < n_buckets - 1 else len(sorted_preds)
        bucket = sorted_preds[start:end]
        if not bucket:
            continue

        mean_pred = sum(p for p, _ in bucket) / len(bucket)
        mean_actual = sum(a for _, a in bucket) / len(bucket)
        label = f"{mean_pred:.3f}"
        buckets[label] = (mean_pred, mean_actual)

    return buckets


def evaluate(
    predictions_and_actuals: list[tuple[Prediction, int]],
    model_name: str,
    stat: str,
) -> EvaluationResult:
    """Compute all evaluation metrics from a list of (prediction, actual) pairs."""
    pairs = [(p.predicted, a) for p, a in predictions_and_actuals]

    if not pairs:
        return EvaluationResult(
            model_name=model_name,
            stat=stat,
            n_predictions=0,
            mae=0.0,
            poisson_log_likelihood=0.0,
            mean_predicted=0.0,
            mean_actual=0.0,
        )

    return EvaluationResult(
        model_name=model_name,
        stat=stat,
        n_predictions=len(pairs),
        mae=mae(pairs),
        poisson_log_likelihood=poisson_log_likelihood(pairs),
        mean_predicted=sum(p for p, _ in pairs) / len(pairs),
        mean_actual=sum(a for _, a in pairs) / len(pairs),
        calibration_buckets=calibration(pairs),
    )


# =============================================================================
# FORECAST MODEL PROTOCOL
# =============================================================================

class ForecastModel(Protocol):
    """Protocol for forecast models."""

    name: str

    def predict(self, features: FeatureSet) -> dict[str, float]:
        """Predict per-60 rates from features.

        Returns dict mapping stat name -> predicted per-60 rate.
        e.g., {"goals_per_60": 1.8, "assists_per_60": 2.5, "shots_per_60": 9.0}
        """
        ...


# =============================================================================
# BACKTEST HARNESS
# =============================================================================

FORECAST_STATS = [
    "goals_per_60", "assists_per_60", "shots_per_60",
    "hits_per_60", "blocked_per_60",
]

# Mapping from forecast stat name to GameIndividualStats column
STAT_COLUMN_MAP = {
    "goals_per_60": "goals_per_60",
    "assists_per_60": "total_assists_per_60",
    "shots_per_60": "shots_per_60",
    "hits_per_60": "hits_per_60",
    "blocked_per_60": "shots_blocked_per_60",
}


class BacktestHarness:
    """Walk-forward backtest through a season.

    For each game in chronological order:
      For each player in that game:
        1. Build features from ONLY prior games
        2. Generate prediction
        3. Record (prediction, actual) pair
    """

    def __init__(
        self,
        model: ForecastModel,
        feature_extractors: list,
        stat: str = "goals",
        min_games_before_predict: int = 10,
        player_filter: str = "all",  # "forwards", "defense", "all"
        situation: str = "all",
    ):
        self.model = model
        self.feature_extractors = feature_extractors
        self.stat = stat
        self.min_games = min_games_before_predict
        self.player_filter = player_filter
        self.situation = situation

    def run(self, season: str) -> BacktestResult:
        """Run walk-forward backtest on a season.

        Args:
            season: Season string like "20232024"
        """
        from sqlalchemy import distinct

        predictions_and_actuals = []

        with get_session() as session:
            # Get all distinct game dates in the season, chronologically
            game_dates = (
                session.query(distinct(GameIndividualStats.game_date))
                .filter(
                    GameIndividualStats.season == season,
                    GameIndividualStats.situation == self.situation,
                )
                .order_by(GameIndividualStats.game_date)
                .all()
            )
            game_dates = [d[0] for d in game_dates]

            # Get player filter set
            valid_players = self._get_valid_players(session, season)

            # Track games played per player for min_games threshold
            games_played_count: dict[int, int] = defaultdict(int)

            for game_date in game_dates:
                # Get all player stats for this date
                game_stats = (
                    session.query(GameIndividualStats)
                    .filter(
                        GameIndividualStats.game_date == game_date,
                        GameIndividualStats.season == season,
                        GameIndividualStats.situation == self.situation,
                    )
                    .all()
                )

                for gs in game_stats:
                    nhl_id = gs.nhl_id

                    if valid_players is not None and nhl_id not in valid_players:
                        continue

                    # Check min games threshold
                    if games_played_count[nhl_id] < self.min_games:
                        games_played_count[nhl_id] += 1
                        continue

                    # Build features from prior games only
                    feature_set = FeatureSet(
                        nhl_id=nhl_id,
                        game_id=gs.game_id,
                        game_date=game_date,
                    )

                    for extractor in self.feature_extractors:
                        extractor.extract(
                            session, feature_set, before_date=game_date
                        )

                    # Generate prediction
                    pred_values = self.model.predict(feature_set)
                    if self.stat not in pred_values:
                        games_played_count[nhl_id] += 1
                        continue

                    prediction = Prediction(
                        nhl_id=nhl_id,
                        game_id=gs.game_id,
                        game_date=game_date,
                        stat=self.stat,
                        predicted=pred_values[self.stat],
                        model_name=self.model.name,
                    )

                    # Get actual value
                    actual_col = STAT_COLUMN_MAP[self.stat]
                    actual = getattr(gs, actual_col, None)
                    if actual is not None:
                        predictions_and_actuals.append((prediction, actual))

                    games_played_count[nhl_id] += 1

        overall = evaluate(predictions_and_actuals, self.model.name, self.stat)

        return BacktestResult(
            model_name=self.model.name,
            stat=self.stat,
            season=season,
            overall=overall,
            predictions=predictions_and_actuals,
        )

    def _get_valid_players(self, session: Session, season: str) -> set[int] | None:
        """Get set of valid player IDs based on player_filter."""
        if self.player_filter == "all":
            return None

        players = session.query(Player).all()
        valid = set()
        for p in players:
            if self.player_filter == "forwards" and p.position in ("C", "LW", "RW"):
                valid.add(p.nhl_id)
            elif self.player_filter == "defense" and p.position == "D":
                valid.add(p.nhl_id)

        return valid

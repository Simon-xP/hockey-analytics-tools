"""Player performance forecasting module.

Public API:
    forecast(nhl_id, game_date) — predict per-game stats for a player
    backtest(model_name, season, stat) — run walk-forward backtest
    compare_models(model_names, season, stat) — compare multiple models
"""

from datetime import date

from src.core.db import get_session
from src.tools.forecasting.baselines import SeasonAverageModel, WeightedBlendModel
from src.tools.forecasting.evaluation import (
    FORECAST_STATS,
    BacktestHarness,
)
from src.tools.forecasting.features import (
    GameContextExtractor,
    RollingIndividualExtractor,
    RollingOnIceExtractor,
    SeasonAggregateExtractor,
)
from src.tools.forecasting.models import (
    BacktestResult,
    EvaluationResult,
    FeatureSet,
    Prediction,
)

# Registry of available models
MODELS = {
    "season_average": SeasonAverageModel,
    "weighted_blend": WeightedBlendModel,
}

# Default feature extractors
# Note: RollingOnIceExtractor omitted — we only scrape individual game logs
# (1 request/player) to stay within NST rate limits. On-ice can be added later.
DEFAULT_EXTRACTORS = [
    RollingIndividualExtractor(),
    SeasonAggregateExtractor(),
    GameContextExtractor(),
]


def _get_model(name: str):
    """Get model instance by name."""
    if name not in MODELS:
        raise ValueError(f"Unknown model: {name}. Available: {list(MODELS.keys())}")
    return MODELS[name]()


def forecast(
    nhl_id: int,
    game_date: date,
    game_id: int | None = None,
    model_name: str = "weighted_blend",
) -> dict[str, float]:
    """Predict per-60 rates for a player in a specific game.

    Args:
        nhl_id: NHL player ID
        game_date: Date of the game
        game_id: Optional NHL game ID (if schedule data is available)
        model_name: Name of model to use

    Returns:
        Dict mapping stat name to predicted per-60 rate.
        e.g., {"goals_per_60": 1.8, "assists_per_60": 2.5}
    """
    model = _get_model(model_name)

    with get_session() as session:
        fs = FeatureSet(
            nhl_id=nhl_id,
            game_id=game_id,
            game_date=game_date,
        )

        for extractor in DEFAULT_EXTRACTORS:
            extractor.extract(session, fs, before_date=game_date)

    return model.predict(fs)


def backtest(
    model_name: str,
    season: str,
    stat: str = "goals_per_60",
    min_games: int = 10,
    player_filter: str = "all",
) -> BacktestResult:
    """Run walk-forward backtest on a season.

    Args:
        model_name: Name of model to test
        season: Season string like "20232024"
        stat: Per-60 stat to evaluate ("goals_per_60", "assists_per_60", etc.)
        min_games: Minimum games before generating predictions
        player_filter: "all", "forwards", or "defense"

    Returns:
        BacktestResult with metrics and all predictions
    """
    model = _get_model(model_name)

    harness = BacktestHarness(
        model=model,
        feature_extractors=DEFAULT_EXTRACTORS,
        stat=stat,
        min_games_before_predict=min_games,
        player_filter=player_filter,
    )

    result = harness.run(season)

    print(result.overall.summary())
    return result


def compare_models(
    model_names: list[str],
    season: str,
    stat: str = "goals_per_60",
    min_games: int = 10,
) -> list[BacktestResult]:
    """Compare multiple models on the same backtest.

    Args:
        model_names: List of model names to compare
        season: Season string like "20232024"
        stat: Stat to evaluate
        min_games: Minimum games before generating predictions

    Returns:
        List of BacktestResult, one per model
    """
    results = []

    print(f"Comparing models on {season} — {stat}")
    print("=" * 70)

    for name in model_names:
        result = backtest(name, season, stat, min_games)
        results.append(result)

    print()
    print("=" * 70)
    header = (
        f"{'Model':<20} {'MAE':>8} {'PoissonLL':>12} "
        f"{'N':>8} {'MeanPred':>10} {'MeanActual':>12}"
    )
    print(header)
    print("-" * 70)
    for r in results:
        o = r.overall
        print(
            f"{o.model_name:<20} {o.mae:>8.4f} {o.poisson_log_likelihood:>12.4f} "
            f"{o.n_predictions:>8} {o.mean_predicted:>10.4f} {o.mean_actual:>12.4f}"
        )

    return results


__all__ = [
    "forecast",
    "backtest",
    "compare_models",
    # Models
    "SeasonAverageModel",
    "WeightedBlendModel",
    "MODELS",
    # Evaluation
    "BacktestHarness",
    "FORECAST_STATS",
    # Features
    "RollingIndividualExtractor",
    "RollingOnIceExtractor",
    "SeasonAggregateExtractor",
    "GameContextExtractor",
    # Data models
    "FeatureSet",
    "Prediction",
    "EvaluationResult",
    "BacktestResult",
]

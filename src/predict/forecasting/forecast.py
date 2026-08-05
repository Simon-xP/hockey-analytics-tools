"""Single-game forecast for a player using the v2 situation-split models.

This is the canonical forecast path used by the evaluation backtest, the
API, and ad-hoc scripts. It handles loading models, computing predicted
per-60 rates per situation (with empirical Bayes blending for PP and PK),
predicting TOI, and combining into per-game projections.

`forecast_player` returns the fantasy-point total the rest of the system
should use. Two things happen on top of the situation model's own output:

- **PIM** is added (`pim.py`). It is a scoring category the per-60 pipeline
  does not cover.
- **Calibration** is applied (`calibration.py`). The raw model over-projects,
  which matters to anything reading a projected total against a threshold.
  `projection["fpts_raw"]` preserves the uncorrected number.

`project_per_game` is unchanged and still returns the raw model output, so
anything measuring the model itself (the walk-forward harness, the variance
fit) sees what the model actually said.
"""

from datetime import date
from pathlib import Path

from src.core.models import Player
from src.core.scoring import SKATER_WEIGHTS
from src.predict.forecasting.calibration import calibrate_fpts
from src.predict.forecasting.constants import SITUATION_CONFIGS
from src.predict.forecasting.empirical_bayes import (
    EmpiricalBayesPredictor,
    blend_xgb_with_eb,
)
from src.predict.forecasting.features import extract_all_features
from src.predict.forecasting.model import SituationModel
from src.predict.forecasting.pim import project_pim_per_game
from src.predict.forecasting.projections import project_per_game
from src.predict.forecasting.toi_model import TOIPredictor

MODEL_DIR = Path("models/forecasting_v2")


def load_models(model_dir: Path = MODEL_DIR) -> dict[str, SituationModel]:
    """Load all trained SituationModels from disk."""
    models = {}
    for situation in SITUATION_CONFIGS:
        path = model_dir / f"{situation}_model.pkl"
        if path.exists():
            models[situation] = SituationModel.load(path)
    return models


def predict_situation_rates(
    session,
    player_id: int,
    game_date: date,
    team_id: int,
    opp_team_id: int,
    home_team_id: int,
    position: str,
    start_year: int,
    models: dict[str, SituationModel],
    toi_predictor: TOIPredictor,
    eb_pp: EmpiricalBayesPredictor,
    eb_pk: EmpiricalBayesPredictor,
    eb_5v5: EmpiricalBayesPredictor | None = None,
    as_of: date | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Predict per-60 rates and TOI for all situations for one player-game.

    Args:
        as_of: Temporal cutoff for data access. When provided, all data
            queries are limited to games before this date.

    Returns (predicted_rates_by_situation, predicted_toi_by_situation).
    """
    cutoff = min(game_date, as_of) if as_of is not None else game_date
    predicted_rates: dict[str, dict[str, float]] = {}
    predicted_toi: dict[str, float] = {}
    is_b2b = False

    for situation in SITUATION_CONFIGS:
        sit_toi = toi_predictor.predict(
            session, player_id, situation, game_date, start_year, is_b2b,
            as_of=cutoff,
        )
        predicted_toi[situation] = sit_toi

        if situation == "other":
            predicted_rates[situation] = {
                "goals_per60": 2.38,
                "assists_per60": 3.06,
            }
            continue

        if situation == "pk":
            eb = eb_pk.predict(session, player_id, cutoff)
            rates = {
                "goals_per60": eb.get("goals_per60", 0),
                "assists_per60": eb.get("assists_per60", 0),
            }
            if situation in models:
                feats = extract_all_features(
                    session, player_id, situation, game_date,
                    team_id, opp_team_id, home_team_id, position, start_year,
                    as_of=cutoff,
                )
                poisson_rates = models[situation].predict(
                    feats, toi_seconds=sit_toi,
                )
                rates.update(poisson_rates)
            predicted_rates[situation] = rates
            continue

        if situation == "pp" and situation in models:
            feats = extract_all_features(
                session, player_id, situation, game_date,
                team_id, opp_team_id, home_team_id, position, start_year,
                as_of=cutoff,
            )
            is_b2b = feats.get("is_b2b", 0) == 1.0
            xgb_rates = models[situation].predict(feats)
            eb_rates = eb_pp.predict(session, player_id, cutoff)
            predicted_rates[situation] = blend_xgb_with_eb(xgb_rates, eb_rates)
            continue

        if situation in models:
            feats = extract_all_features(
                session, player_id, situation, game_date,
                team_id, opp_team_id, home_team_id, position, start_year,
                as_of=cutoff,
            )
            is_b2b = feats.get("is_b2b", 0) == 1.0
            xgb_rates = models[situation].predict(feats)
            if situation == "5v5" and eb_5v5 is not None:
                eb_rates = eb_5v5.predict(session, player_id, cutoff)
                xgb_rates = blend_xgb_with_eb(
                    xgb_rates, eb_rates, only_stats=["goals", "assists"],
                )
            predicted_rates[situation] = xgb_rates

    return predicted_rates, predicted_toi


def forecast_player(
    session,
    nhl_id: int,
    game_date: date,
    opp_team_id: int | None = None,
    home_team_id: int | None = None,
    models: dict[str, SituationModel] | None = None,
    toi_predictor: TOIPredictor | None = None,
    eb_pp: EmpiricalBayesPredictor | None = None,
    eb_pk: EmpiricalBayesPredictor | None = None,
    eb_5v5: EmpiricalBayesPredictor | None = None,
    as_of: date | None = None,
) -> dict[str, float]:
    """Forecast a single game for one player.

    When opp_team_id / home_team_id are not given, uses the player's most
    recent game as a proxy (matches behavior of ad-hoc next-game forecasts).

    Args:
        as_of: Temporal cutoff for data access. When provided, all feature
            extraction and rate estimation is limited to data before this
            date. Use for backtesting to prevent leakage.

    Returns the dict from `project_per_game`: per-game stat projections,
    situation-split projections, and fantasy points.
    """
    from sqlalchemy import text

    cutoff = min(game_date, as_of) if as_of is not None else game_date

    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player:
        raise ValueError(f"Player {nhl_id} not found")

    team_id = player.team_id
    position = player.position

    if opp_team_id is None or home_team_id is None:
        last = session.execute(
            text("""
                SELECT g.game_id, g.home_team_id, g.away_team_id
                FROM game_advanced_stats gas
                JOIN games g ON gas.game_id = g.game_id
                WHERE gas.player_id = :p AND g.date < :d
                ORDER BY g.date DESC LIMIT 1
            """),
            {"p": nhl_id, "d": cutoff},
        ).fetchone()
        if not last:
            raise ValueError(f"No game history for {nhl_id} before {cutoff}")
        home_team_id = last[1]
        opp_team_id = last[2] if last[1] == team_id else last[1]

    if models is None:
        models = load_models()
    if toi_predictor is None:
        toi_predictor = TOIPredictor()
    if eb_pp is None:
        eb_pp = EmpiricalBayesPredictor("pp", ["goals", "assists", "shots"])
    if eb_pk is None:
        eb_pk = EmpiricalBayesPredictor("pk", ["goals", "assists"])
    if eb_5v5 is None:
        eb_5v5 = EmpiricalBayesPredictor("5v5", ["goals", "assists"])

    start_year = game_date.year if game_date.month >= 8 else game_date.year - 1

    predicted_rates, predicted_toi = predict_situation_rates(
        session, nhl_id, game_date, team_id, opp_team_id, home_team_id,
        position, start_year, models, toi_predictor, eb_pp, eb_pk, eb_5v5,
        as_of=cutoff,
    )

    projection = project_per_game(predicted_rates, predicted_toi)

    # The situation model covers goals/assists/shots/hits/blocks. PIM is a
    # scoring category too, and a separate trait-like model, so it is added
    # here rather than folded into the per-60 pipeline.
    projection["pim"] = project_pim_per_game(session, nhl_id, as_of=cutoff)
    projection["fpts_raw"] = projection["fpts"]
    projection["fpts"] = calibrate_fpts(
        projection["fpts"] + projection["pim"] * SKATER_WEIGHTS["pim"]
    )

    return projection

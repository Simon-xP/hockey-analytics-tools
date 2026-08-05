"""Goalie forecasting: what a goalie is worth on a given day, with spread.

The optimizer consumes four numbers per goalie-day, assembled by
`forecast_goalie_day`:

    p_start       probability they start
    start_value   expected fantasy points given a start
    outcome_var   variance of points given a start
    confidence    how firm p_start is, separate from its value

See `docs/plans/weekly-optimizer/04a-goalie-variance.md` for the contract
and `docs/goalie-forecasting.md` for the wider pipeline.
"""

from datetime import date, datetime

from src.predict.goalies.constants import OUTCOME_VAR
from src.predict.goalies.save_quality import (
    SaveQuality,
    estimate_save_quality,
    shrink_save_rate,
)
from src.predict.goalies.start_value import (
    StartInputs,
    StartProjection,
    build_start_inputs,
    forecast_start_value,
    project_start_value,
)
from src.predict.goalies.starts import (
    StartProbability,
    crease_share,
    estimate_start_probability,
)
from src.predict.goalies.variance import GoalieDayForecast, combine

__all__ = [
    "OUTCOME_VAR",
    "SaveQuality", "estimate_save_quality", "shrink_save_rate",
    "StartInputs", "StartProjection", "build_start_inputs",
    "forecast_start_value", "project_start_value",
    "StartProbability", "crease_share", "estimate_start_probability",
    "GoalieDayForecast", "combine",
    "forecast_goalie_day",
]


def forecast_goalie_day(
    session,
    nhl_id: int,
    team_id: int,
    opponent_team_id: int,
    game_id: int,
    game_date: date,
    as_of: datetime,
    is_home: bool,
) -> GoalieDayForecast:
    """The full four-number contract for one goalie on one day.

    `as_of` is a datetime: starting-goalie reports land during game day, so
    what was knowable depends on the hour, not just the date.
    """
    start_prob = estimate_start_probability(
        session, nhl_id=nhl_id, team_id=team_id, game_id=game_id,
        game_date=game_date, as_of=as_of,
    )
    projection = forecast_start_value(
        session, nhl_id=nhl_id, team_id=team_id,
        opponent_team_id=opponent_team_id, as_of=as_of.date(),
        is_home=is_home,
    )

    return GoalieDayForecast(
        nhl_id=nhl_id,
        game_date=game_date,
        game_id=game_id,
        p_start=start_prob.p_start,
        start_value=projection.start_value,
        outcome_var=OUTCOME_VAR,
        confidence=start_prob.confidence,
        source=start_prob.source,
    )

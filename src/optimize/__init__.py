"""Optimization layer — decide what to do with a fantasy roster.

Consumes projections from `src.predict` and answers the three questions that
actually change a lineup:

    Who plays today?        slots.py
    What is a player worth?  value.py, replacement.py, drops.py, goalies.py
    What should I do this week?  week/

`week.optimize_week()` is the main entry point and works for **any** team in
the league — my own team gets the full pickup-level search, opponents get a
cheaper projection of their best possible week. Modelling what an opponent
can do matters as much as modelling my own ceiling.

    from src.optimize import optimize_week
    result = optimize_week(session, league_key, team_key, as_of, week_end,
                           my_team_key=my_key)

`matchup/` sits on top of that: it turns both teams' projections into a win
probability and an aggression level, which feeds back into the heavy path.
"""

from datetime import date
from typing import Callable, Optional

from sqlalchemy.orm import Session

from src.optimize.models import (
    AGGRESSION_WEIGHTS,
    AggressionLevel,
    GoalieStreamScore,
    PlayerType,
    PlayerValue,
    ReplacementLevel,
    Roster,
    RosterPlayer,
    RosterSlotSettings,
    TeamWeekResult,
    TransactionCandidate,
    WeekPlan,
)
from src.optimize.drops import (
    compute_position_scarcity,
    get_drop_candidates,
    rank_drops,
)
from src.optimize.goalies import (
    compute_crease_share,
    compute_opponent_softness,
    evaluate_goalie_stream,
    goalie_stream_to_player_value,
)
from src.optimize.replacement import compute_replacement_level
from src.optimize.slots import assign_players_to_slots, get_teams_playing_on_date
from src.optimize.value import (
    compute_player_value,
    compute_player_value_simple,
    get_team_remaining_games,
    get_team_week_games,
    load_roster_from_yahoo,
)
from src.optimize.week import (
    build_candidates,
    optimize_week,
    optimize_week_heavy,
    optimize_week_light,
    plan_week,
    score_transaction,
)


def evaluate_add(
    session: Session,
    nhl_id: int,
    roster: Roster,
    yahoo_week: int,
    replacement_level: ReplacementLevel,
    season: str = "20252026",
    aggression: AggressionLevel = AggressionLevel.NORMAL,
    forecast_fn: Optional[Callable] = None,
    protected_nhl_ids: Optional[set[int]] = None,
) -> Optional[TransactionCandidate]:
    """Evaluate a specific player as an add target.

    Finds the best drop candidate from the roster and scores the transaction.

    Returns:
        Best TransactionCandidate for this add, or None if no viable
        transaction exists.
    """
    add_value = compute_player_value(
        session, nhl_id, roster, yahoo_week, season, forecast_fn
    )
    if add_value is None:
        return None

    drops = get_drop_candidates(
        session, roster, yahoo_week, replacement_level,
        max_candidates=5, season=season,
        protected_nhl_ids=protected_nhl_ids,
    )
    if not drops:
        return None

    candidates = build_candidates(
        [add_value], drops, replacement_level, aggression
    )

    return candidates[0] if candidates else None


def recommend(
    session: Session,
    roster: Roster,
    yahoo_week: int,
    replacement_level: ReplacementLevel,
    free_agent_nhl_ids: list[int],
    season: str = "20252026",
    adds_remaining: int = 4,
    aggression: AggressionLevel = AggressionLevel.NORMAL,
    forecast_fn: Optional[Callable] = None,
    protected_nhl_ids: Optional[set[int]] = None,
) -> WeekPlan:
    """Generate the optimal WeekPlan from an explicit roster and FA list.

    Lower-level than `optimize_week()`: use this when you already have the
    roster and candidate pool in hand (backtests, tests, ad-hoc analysis).
    `optimize_week()` builds those from the database for you.
    """
    add_targets: list[PlayerValue] = []
    for fa_id in free_agent_nhl_ids:
        pv = compute_player_value(
            session, fa_id, roster, yahoo_week, season, forecast_fn
        )
        if pv is not None and pv.weekly_fpts > 0:
            add_targets.append(pv)

    drop_candidates = get_drop_candidates(
        session, roster, yahoo_week, replacement_level,
        max_candidates=8, season=season,
        protected_nhl_ids=protected_nhl_ids,
    )

    if not add_targets or not drop_candidates:
        return WeekPlan(
            yahoo_week=yahoo_week,
            transactions=[],
            adds_used=0,
            projected_fpts_gain=0.0,
            aggression=aggression,
            reasoning="No viable transactions found",
        )

    return plan_week(
        roster=roster,
        add_targets=add_targets,
        drop_candidates=drop_candidates,
        yahoo_week=yahoo_week,
        replacement=replacement_level,
        adds_remaining=adds_remaining,
        aggression=aggression,
    )


__all__ = [
    # Models
    "AGGRESSION_WEIGHTS",
    "AggressionLevel",
    "GoalieStreamScore",
    "PlayerType",
    "PlayerValue",
    "ReplacementLevel",
    "Roster",
    "RosterPlayer",
    "RosterSlotSettings",
    "TeamWeekResult",
    "TransactionCandidate",
    "WeekPlan",
    # Lineup slots
    "assign_players_to_slots",
    "get_teams_playing_on_date",
    # Player valuation
    "compute_player_value",
    "compute_player_value_simple",
    "get_team_remaining_games",
    "get_team_week_games",
    "load_roster_from_yahoo",
    "compute_replacement_level",
    # Drop ranking
    "compute_position_scarcity",
    "get_drop_candidates",
    "rank_drops",
    # Goalie streaming
    "compute_crease_share",
    "compute_opponent_softness",
    "evaluate_goalie_stream",
    "goalie_stream_to_player_value",
    # Week optimization
    "optimize_week",
    "optimize_week_heavy",
    "optimize_week_light",
    "plan_week",
    "build_candidates",
    "score_transaction",
    # High-level helpers
    "evaluate_add",
    "recommend",
]

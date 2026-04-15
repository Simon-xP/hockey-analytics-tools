"""Transaction evaluator for PuckAgent.

Core decision engine: should we add/drop a player?

Public API:
    compute_player_value(session, nhl_id, roster, yahoo_week) — slot-aware weekly FPTS
    compute_replacement_level(session, free_agents) — FA pool baseline
    rank_drops(roster, yahoo_week, replacement_level) — worst droppable players
    evaluate_add(session, nhl_id, roster, ...) — score a specific add target
    recommend(session, roster, ...) — generate optimal WeekPlan
"""

from datetime import date
from typing import Callable, Optional

from sqlalchemy.orm import Session

from src.core.models import Player
from src.tools.schedule.models import Roster
from src.tools.transactions.models import (
    AggressionLevel,
    AGGRESSION_WEIGHTS,
    GoalieStreamScore,
    PlayerType,
    PlayerValue,
    ReplacementLevel,
    TransactionCandidate,
    WeekPlan,
)
from src.tools.transactions.player_value import (
    compute_player_value,
    compute_player_value_simple,
    get_team_remaining_games,
    get_team_week_games,
    load_roster_from_yahoo,
)
from src.tools.transactions.replacement_level import compute_replacement_level
from src.tools.transactions.drop_ranker import (
    compute_position_scarcity,
    get_drop_candidates,
    rank_drops,
)
from src.tools.transactions.weekly_optimizer import (
    build_candidates,
    optimize_week,
    score_transaction,
)
from src.tools.transactions.upside import (
    compute_upside_score,
    compute_upside_breakdown,
    hold_patience_games,
)
from src.tools.transactions.desperation import (
    compute_aggression,
    compute_aggression_from_yahoo,
)
from src.tools.transactions.goalie_eval import (
    compute_crease_share,
    compute_opponent_softness,
    evaluate_goalie_stream,
    goalie_stream_to_player_value,
)
from src.tools.transactions.backtest import (
    BacktestResult,
    TransactionBacktester,
    WeekBacktestResult,
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

    Finds the best drop candidate from the roster and scores the
    transaction.

    Args:
        session: DB session
        nhl_id: NHL ID of the player to evaluate adding
        roster: Current fantasy roster
        yahoo_week: Week to evaluate for
        replacement_level: FA baseline
        season: Season string
        aggression: Matchup context
        forecast_fn: Optional forecast override
        protected_nhl_ids: Players that cannot be dropped

    Returns:
        Best TransactionCandidate for this add, or None if no viable
        transaction exists.
    """
    # Value the add target
    add_value = compute_player_value(
        session, nhl_id, roster, yahoo_week, season, forecast_fn
    )
    if add_value is None:
        return None

    # Get drop candidates
    drops = get_drop_candidates(
        session, roster, yahoo_week, replacement_level,
        max_candidates=5, season=season,
        protected_nhl_ids=protected_nhl_ids,
    )
    if not drops:
        return None

    # Score against each drop candidate, return best
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
    """Generate the optimal WeekPlan — up to `adds_remaining` transactions.

    Args:
        session: DB session
        roster: Current fantasy roster
        yahoo_week: Week to optimize for
        replacement_level: FA baseline
        free_agent_nhl_ids: NHL IDs of free agents to consider
        season: Season string
        adds_remaining: Weekly add budget (typically 4)
        aggression: Matchup context
        forecast_fn: Optional forecast override
        protected_nhl_ids: Players that cannot be dropped

    Returns:
        WeekPlan with recommended transactions.
    """
    # Value all free agent targets
    add_targets: list[PlayerValue] = []
    for fa_id in free_agent_nhl_ids:
        pv = compute_player_value(
            session, fa_id, roster, yahoo_week, season, forecast_fn
        )
        if pv is not None and pv.weekly_fpts > 0:
            add_targets.append(pv)

    # Get drop candidates
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

    return optimize_week(
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
    "AggressionLevel",
    "AGGRESSION_WEIGHTS",
    "GoalieStreamScore",
    "PlayerType",
    "PlayerValue",
    "ReplacementLevel",
    "TransactionCandidate",
    "WeekPlan",
    # Player valuation
    "compute_player_value",
    "compute_player_value_simple",
    "get_team_remaining_games",
    "get_team_week_games",
    # Replacement level
    "compute_replacement_level",
    # Drop ranking
    "compute_position_scarcity",
    "get_drop_candidates",
    "rank_drops",
    # Transaction scoring
    "build_candidates",
    "optimize_week",
    "score_transaction",
    # Upside
    "compute_upside_score",
    "compute_upside_breakdown",
    "hold_patience_games",
    # Desperation
    "compute_aggression",
    "compute_aggression_from_yahoo",
    # Goalie evaluation
    "compute_crease_share",
    "compute_opponent_softness",
    "evaluate_goalie_stream",
    "goalie_stream_to_player_value",
    # Backtest
    "BacktestResult",
    "TransactionBacktester",
    "WeekBacktestResult",
    # Public API
    "evaluate_add",
    "recommend",
]

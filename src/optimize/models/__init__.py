"""Dataclasses shared across the optimize layer.

Four groups, deliberately kept apart:

- `roster`  — league configuration and roster membership (slots, players)
- `value`   — what a single player is worth over a window
- `plan`    — a scored add/drop pair and the week plan it belongs to
- `matchup` — head-to-head state: both teams' projections and win probability

Everything is re-exported here, so callers can just
`from src.optimize.models import PlayerValue, Roster, ...`.
"""

from src.optimize.models.roster import Roster, RosterPlayer, RosterSlotSettings
from src.optimize.models.value import (
    GoalieStreamScore,
    PlayerType,
    PlayerValue,
    ReplacementLevel,
)
from src.optimize.models.plan import (
    AGGRESSION_WEIGHTS,
    AggressionLevel,
    TeamWeekResult,
    TransactionCandidate,
    WeekPlan,
)
from src.optimize.models.matchup import (
    MatchupContext,
    MatchupSnapshot,
    PickupBoost,
    TeamProjection,
    WeekImportance,
    WinProbability,
)

__all__ = [
    # Roster / league config
    "Roster",
    "RosterPlayer",
    "RosterSlotSettings",
    # Player value
    "GoalieStreamScore",
    "PlayerType",
    "PlayerValue",
    "ReplacementLevel",
    # Transaction planning
    "AGGRESSION_WEIGHTS",
    "AggressionLevel",
    "TeamWeekResult",
    "TransactionCandidate",
    "WeekPlan",
    # Matchup state
    "MatchupContext",
    "MatchupSnapshot",
    "PickupBoost",
    "TeamProjection",
    "WeekImportance",
    "WinProbability",
]

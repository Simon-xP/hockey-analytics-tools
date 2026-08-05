"""Week optimization for any team in the league.

`optimize_week()` is the single entry point. Pass it a team and it picks the
right depth:

- **my team** → `heavy`. Values every free agent at the pickup-player level,
  ranks drop candidates, searches (add, drop) pairs, and weights everything
  by the current aggression level. Returns a plan you can actually execute.
- **any other team** → `light`. Projects their roster with the same
  prediction module, then assumes they take the best available add path.
  No aggression, no drop ranking, meaningfully cheaper to run.

Knowing how many points an opponent can put up matters as much as knowing
your own ceiling, so both go through the same call and return the same
`TeamWeekResult`.

    result = optimize_week(session, league_key, team_key, my_team_key, ...)
    result.expected_total       # earned + projected + pickups
    result.plan                 # transactions to execute (heavy only)
"""

from datetime import date

from sqlalchemy.orm import Session

from src.optimize.models import (
    AggressionLevel,
    RosterSlotSettings,
    TeamWeekResult,
)
from src.optimize.week.heavy import (
    build_candidates,
    optimize_week_heavy,
    plan_week,
    score_transaction,
)
from src.optimize.week.light import (
    model_pickup_boost,
    optimize_week_light,
    project_team_remaining,
)


def optimize_week(
    session: Session,
    league_key: str,
    team_key: str,
    as_of: date,
    week_end: date,
    my_team_key: str | None = None,
    yahoo_week: int = 0,
    earned: float = 0.0,
    adds_remaining: int = 4,
    aggression: AggressionLevel = AggressionLevel.NORMAL,
    season: str = "20252026",
    roster_slot_settings: RosterSlotSettings | None = None,
    force_depth: str | None = None,
    **kwargs,
) -> TeamWeekResult:
    """Optimize the rest of the fantasy week for `team_key`.

    Args:
        session: DB session.
        league_key: Yahoo league key.
        team_key: The team to optimize.
        as_of: Decision date. Nothing after this date is visible.
        week_end: Last day of the fantasy week (inclusive).
        my_team_key: My own team. When it matches `team_key`, the heavy path
            runs. Leave as None to always take the light path.
        yahoo_week: Fantasy week number (heavy path only).
        earned: Points already banked this week.
        adds_remaining: Add budget left for the week.
        aggression: How hard to stream (heavy path only — ignored for
            opponents, whose aggression is unknowable).
        season: Season string for stats lookups.
        roster_slot_settings: League slot configuration.
        force_depth: "light" or "heavy" to override the dispatch, mainly for
            benchmarking the two paths against each other.

    Returns:
        TeamWeekResult. `plan` is populated on the heavy path only.
    """
    depth = force_depth or (
        "heavy" if my_team_key is not None and team_key == my_team_key else "light"
    )

    if depth == "heavy":
        return optimize_week_heavy(
            session, league_key, team_key,
            as_of=as_of, week_end=week_end, yahoo_week=yahoo_week,
            earned=earned, adds_remaining=adds_remaining,
            aggression=aggression, season=season,
            roster_slot_settings=roster_slot_settings,
            **kwargs,
        )

    return optimize_week_light(
        session, league_key, team_key,
        as_of=as_of, week_end=week_end,
        earned=earned, adds_remaining=adds_remaining,
        roster_slot_settings=roster_slot_settings,
        **kwargs,
    )


__all__ = [
    "optimize_week",
    "optimize_week_heavy",
    "optimize_week_light",
    "plan_week",
    "build_candidates",
    "score_transaction",
    "project_team_remaining",
    "model_pickup_boost",
]

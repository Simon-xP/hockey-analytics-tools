"""Builders for hand-made `WeekState`s.

The grid is pure arithmetic over a projection cache, so its tests need no
database at all. That is the point of the substrate design: everything below
the planner reads from one immutable snapshot, and a snapshot is cheap to
fabricate.
"""

from datetime import date, timedelta

import pytest

from src.optimize.models import RosterSlotSettings
from src.optimize.models.week import (
    LeagueSettings,
    ProjectionCache,
    RosterEntry,
    WeekState,
)

MONDAY = date(2026, 1, 12)
TUESDAY = MONDAY + timedelta(days=1)
WEDNESDAY = MONDAY + timedelta(days=2)
THURSDAY = MONDAY + timedelta(days=3)
SUNDAY = MONDAY + timedelta(days=6)


def small_slots() -> RosterSlotSettings:
    """A deliberately tight league: every slot decision is forced."""
    return RosterSlotSettings(c=2, lw=1, rw=1, d=2, g=1, util=1, bn=3, ir=1, ir_plus=1)


def league(slots: RosterSlotSettings | None = None) -> LeagueSettings:
    slots = slots or small_slots()
    return LeagueSettings(
        league_key="nhl.l.test",
        slots=slots,
        n_teams=16,
        adds_per_week=4,
        waiver_days=2,
        roster_size=(
            slots.c + slots.lw + slots.rw + slots.d + slots.g + slots.util + slots.bn
        ),
    )


def entry(
    nhl_id: int,
    name: str,
    positions: tuple[str, ...],
    team_abbrev: str = "TOR",
    **kwargs,
) -> RosterEntry:
    return RosterEntry(
        nhl_id=nhl_id,
        name=name,
        team_abbrev=team_abbrev,
        positions=positions,
        injury_status=kwargs.get("injury_status"),
        expected_return=kwargs.get("expected_return"),
        ir_eligible=kwargs.get("ir_eligible", False),
        is_protected=kwargs.get("is_protected", False),
    )


def make_state(
    roster: tuple[RosterEntry, ...],
    projections: dict[tuple[int, date], float],
    *,
    schedule: dict[date, frozenset[str]] | None = None,
    slots: RosterSlotSettings | None = None,
    as_of: date = MONDAY,
    week_start: date = MONDAY,
    week_end: date = SUNDAY,
    **overrides,
) -> WeekState:
    """A `WeekState` with everything the grid reads and nothing it does not.

    When `schedule` is omitted it is derived from the projection cache: a team
    plays on a day exactly when one of its players has a projection for it.
    """
    if schedule is None:
        by_team: dict[date, set[str]] = {}
        team_of = {e.nhl_id: e.team_abbrev for e in roster}
        for (nhl_id, day) in projections:
            by_team.setdefault(day, set()).add(team_of.get(nhl_id, "FA"))
        schedule = {day: frozenset(teams) for day, teams in by_team.items()}

    defaults = dict(
        my_earned=0.0,
        opp_earned=0.0,
        adds_remaining=4,
        opp_adds_remaining=4,
        open_active_spots=0,
        open_ir_spots=1,
        my_team_key="nhl.l.test.t.1",
        opp_team_key="nhl.l.test.t.2",
    )
    defaults.update(overrides)

    return WeekState(
        as_of=as_of,
        week_start=week_start,
        week_end=week_end,
        league=league(slots),
        roster=roster,
        schedule=schedule,
        projections=ProjectionCache(values=projections),
        **defaults,
    )


@pytest.fixture
def monday() -> date:
    return MONDAY

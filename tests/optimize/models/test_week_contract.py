"""Contract tests for the weekly optimizer types.

Every type in `src/optimize/models/week.py` is constructed here fully
populated. This file exists to break loudly when a downstream package
silently changes a field that other packages depend on.
"""

import dataclasses
from datetime import date, datetime

import pytest

from src.optimize.models.matchup import WeekImportance
from src.optimize.models.plan import AggressionLevel
from src.optimize.models.roster import RosterSlotSettings
from src.optimize.models.value import PlayerType
from src.optimize.models.week import (
    AGGRESSIVE,
    CONSERVATIVE,
    DESPERATE,
    NORMAL,
    Candidate,
    CandidateSource,
    DaySlate,
    DropFloor,
    GameSigmaFn,
    GoalieGameVarFn,
    HoldValue,
    LeagueSettings,
    LineupGrid,
    PlannedMove,
    PlanSet,
    Posture,
    PostureMode,
    ProjectionCache,
    ProjectionProvider,
    RosterEntry,
    WeekPlan,
    WeekState,
)

WINDOW_START = date(2026, 1, 12)  # Monday
WINDOW_END = date(2026, 1, 18)  # Sunday


# ---------------------------------------------------------------------------
# Builders: one per type, fully populated
# ---------------------------------------------------------------------------


def make_posture() -> Posture:
    return Posture(
        mode=PostureMode.CONTEST,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        aggression=AggressionLevel.AGGRESSIVE,
        p_win=0.52,
        importance=WeekImportance.BIG,
        reasoning=("Matchup is within 6 points with 4 days left.",),
    )


def make_league() -> LeagueSettings:
    return LeagueSettings(
        league_key="453.l.12345",
        slots=RosterSlotSettings(),
        n_teams=16,
        adds_per_week=4,
        waiver_days=2,
        roster_size=17,
    )


def make_roster_entry(nhl_id: int = 8478402) -> RosterEntry:
    return RosterEntry(
        nhl_id=nhl_id,
        name="Connor McDavid",
        team_abbrev="EDM",
        positions=("C",),
        injury_status="DTD",
        expected_return=date(2026, 1, 14),
        ir_eligible=False,
        is_protected=True,
    )


def make_projection_cache() -> ProjectionCache:
    return ProjectionCache(
        values={
            (8478402, WINDOW_START): 6.2,
            (8477492, WINDOW_START): 4.1,
        }
    )


def make_state() -> WeekState:
    return WeekState(
        as_of=date(2026, 1, 14),
        week_start=WINDOW_START,
        week_end=WINDOW_END,
        league=make_league(),
        my_team_key="453.l.12345.t.3",
        opp_team_key="453.l.12345.t.9",
        roster=(make_roster_entry(), make_roster_entry(8477492)),
        my_earned=61.4,
        opp_earned=55.2,
        adds_remaining=3,
        opp_adds_remaining=1,
        open_active_spots=1,
        open_ir_spots=2,
        schedule={WINDOW_START: frozenset({"EDM", "TOR"})},
        projections=make_projection_cache(),
    )


def make_day_slate(day: date = WINDOW_START) -> DaySlate:
    return DaySlate(
        day=day,
        starters=(8478402, 8477492),
        benched=(8479318,),
        open_slots={"C": 0, "LW": 1, "RW": 0, "D": 2, "G": 1, "UTIL": 0},
        mu=41.8,
        var=169.0,
    )


def make_candidate(nhl_id: int = 8480012) -> Candidate:
    return Candidate(
        nhl_id=nhl_id,
        name="Some Streamer",
        team_abbrev="SEA",
        positions=("LW", "RW"),
        player_type=PlayerType.SKATER,
        projections={WINDOW_START: 3.9, date(2026, 1, 15): 4.2},
        terminal_value=11.5,
        available_from=date(2026, 1, 15),
        upside_score=0.3,
        opportunity_score=-0.1,
        confidence=0.75,
        source="waiver",
    )


def make_hold_value() -> HoldValue:
    return HoldValue(
        nhl_id=8477492,
        ros_over_replacement=22.6,
        window_contribution=4.1,
        upside_option=1.2,
        scarcity=0.4,
        total=28.3,
        protected=False,
        reasoning=("Third-line minutes but the only RW depth we have.",),
    )


def make_planned_move() -> PlannedMove:
    return PlannedMove(
        fire_date=date(2026, 1, 15),
        add=make_candidate(),
        drop=make_roster_entry(8477492),
        is_ir_move=False,
        delta_p_win=0.031,
        delta_terminal=-2.4,
        alternates=(make_candidate(8480013), make_candidate(8480014)),
        reasoning=("Three games to our two, and he clears waivers Thursday.",),
    )


def make_week_plan(plan_id: str = "plan-001") -> WeekPlan:
    return WeekPlan(
        plan_id=plan_id,
        generated_at=datetime(2026, 1, 14, 9, 30, 0),
        state_fingerprint="a3f1c2",
        posture=make_posture(),
        moves=(make_planned_move(),),
        baseline_p_win=0.52,
        projected_p_win=0.551,
        adds_used=1,
        conviction=0.68,
        fire_now=(0,),
        reasoning=("One swap now, hold the other add for Saturday's slate.",),
    )


# ---------------------------------------------------------------------------
# Fakes for the protocols
# ---------------------------------------------------------------------------


class FakeGrid:
    """Minimal structural implementation of `LineupGrid`."""

    def __init__(self) -> None:
        self.window_start = WINDOW_START
        self.window_end = WINDOW_END
        self.days = (make_day_slate(),)

    def mu(self) -> float:
        return sum(d.mu for d in self.days)

    def sigma(self) -> float:
        return sum(d.var for d in self.days) ** 0.5

    def with_move(self, add, drop, on_date) -> "FakeGrid":
        return FakeGrid()

    def marginal(self, candidate, on_date) -> tuple[float, float]:
        return (candidate.projections.get(on_date, 0.0), 4.0)


class FakeProjections:
    def project(self, nhl_id: int, game_date: date) -> float:
        return 4.0


class FakeSource:
    def candidates(self, state, grid, posture) -> list[Candidate]:
        return [make_candidate()]


def fake_game_sigma(projected_fpts: float, player_type: PlayerType) -> float:
    return 0.45 * projected_fpts


def fake_goalie_game_var(p_start: float, start_value: float, outcome_var: float) -> float:
    return p_start * outcome_var + p_start * (1 - p_start) * start_value**2


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

ALL_INSTANCES = [
    make_posture(),
    make_league(),
    make_roster_entry(),
    make_state(),
    make_day_slate(),
    make_candidate(),
    make_hold_value(),
    DropFloor(aggression=AggressionLevel.NORMAL, threshold=15.0),
    make_planned_move(),
    make_week_plan(),
    PlanSet(best=make_week_plan(), alternates=(make_week_plan("plan-002"),)),
]


@pytest.mark.parametrize("instance", ALL_INSTANCES, ids=lambda i: type(i).__name__)
def test_every_type_is_frozen(instance):
    field_name = dataclasses.fields(instance)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field_name, None)


@pytest.mark.parametrize("instance", ALL_INSTANCES, ids=lambda i: type(i).__name__)
def test_every_type_survives_replace(instance):
    assert dataclasses.replace(instance) == instance


def test_hashable_types_are_hashable():
    """Types the planner keys collections on must hash."""
    for instance in (
        make_posture(),
        make_roster_entry(),
        make_hold_value(),
        DropFloor(aggression=AggressionLevel.NORMAL, threshold=15.0),
    ):
        assert isinstance(hash(instance), int)


def test_week_py_does_not_import_the_week_package():
    """The contract must not depend on anything it blocks."""
    import inspect

    from src.optimize.models import week

    source = inspect.getsource(week)
    assert "src.optimize.week" not in source


def test_intensity_members_are_reexported():
    assert (CONSERVATIVE, NORMAL, AGGRESSIVE, DESPERATE) == (
        AggressionLevel.CONSERVATIVE,
        AggressionLevel.NORMAL,
        AggressionLevel.AGGRESSIVE,
        AggressionLevel.DESPERATE,
    )


def test_prepare_is_not_reexported():
    """PREPARE is dead on this axis. It survives in plan.py only for P7."""
    from src.optimize.models import week

    assert not hasattr(week, "PREPARE")
    assert AggressionLevel.PREPARE not in (CONSERVATIVE, NORMAL, AGGRESSIVE, DESPERATE)


def test_posture_modes():
    assert PostureMode.CONTEST.value == "contest"
    assert PostureMode.PUNT.value == "punt"


def test_punt_posture_still_carries_p_win():
    """The UI needs to show why we gave up."""
    punt = dataclasses.replace(
        make_posture(),
        mode=PostureMode.PUNT,
        window_start=date(2026, 1, 19),
        window_end=date(2026, 1, 25),
        p_win=0.04,
    )
    assert punt.p_win == 0.04


def test_candidate_omits_days_without_a_game():
    """Absent, not zero. A zero would read as a real projection."""
    candidate = make_candidate()
    assert date(2026, 1, 13) not in candidate.projections
    assert set(candidate.projections) == {WINDOW_START, date(2026, 1, 15)}


def test_drop_floor_permits_below_threshold():
    floor = DropFloor(aggression=AggressionLevel.NORMAL, threshold=30.0)
    assert floor.permits(make_hold_value())


def test_drop_floor_blocks_above_threshold():
    floor = DropFloor(aggression=AggressionLevel.CONSERVATIVE, threshold=10.0)
    assert not floor.permits(make_hold_value())


def test_drop_floor_never_permits_a_protected_player():
    """Protection is absolute, whatever the floor is."""
    floor = DropFloor(aggression=AggressionLevel.DESPERATE, threshold=1e9)
    protected = dataclasses.replace(make_hold_value(), protected=True, total=0.0)
    assert not floor.permits(protected)


def test_grid_satisfies_the_protocol():
    grid: LineupGrid = FakeGrid()
    assert grid.mu() == pytest.approx(41.8)
    assert grid.sigma() == pytest.approx(13.0)
    assert grid.marginal(make_candidate(), WINDOW_START) == (3.9, 4.0)
    assert isinstance(grid.with_move(make_candidate(), 8477492, WINDOW_START), FakeGrid)


def test_provider_protocols_are_satisfiable():
    projections: ProjectionProvider = FakeProjections()
    source: CandidateSource = FakeSource()
    assert projections.project(8478402, WINDOW_START) == 4.0
    assert source.candidates(make_state(), FakeGrid(), make_posture())[0].nhl_id == 8480012


def test_variance_signatures_are_satisfiable():
    game_sigma: GameSigmaFn = fake_game_sigma
    goalie_game_var: GoalieGameVarFn = fake_goalie_game_var
    assert game_sigma(10.0, PlayerType.SKATER) == pytest.approx(4.5)
    assert goalie_game_var(0.6, 12.0, 9.0) > 0


def test_plan_set_carries_both_levels_of_alternate():
    plan_set = PlanSet(best=make_week_plan(), alternates=(make_week_plan("plan-002"),))
    assert plan_set.best.moves[0].alternates  # per-move backups
    assert plan_set.alternates  # whole-plan alternatives

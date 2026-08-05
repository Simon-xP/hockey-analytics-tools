"""Shared types for the weekly optimizer.

Every package in the weekly-optimizer rebuild speaks these types. This module
is pure contract: dataclasses, enums, and protocols. No database access, no
imports from `src/optimize/week/`, no computation beyond `DropFloor.permits`.

Design rules (see `docs/plans/weekly-optimizer/01-contract.md`):

- Frozen dataclasses by default. `WeekState` and everything reachable from it
  is an immutable snapshot taken at a single `as_of`; mutation is how time
  leakage gets in.
- Tuples, not lists, on frozen types. `Mapping`, not `dict`.
- No optional fields that hide missing data. If a projection could not be
  computed, the candidate should not exist.
- Every type the planner produces carries `reasoning: tuple[str, ...]`,
  written for a fantasy manager rather than a debugger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Mapping, Protocol

from src.optimize.models.matchup import WeekImportance
from src.optimize.models.plan import AggressionLevel
from src.optimize.models.roster import RosterSlotSettings
from src.optimize.models.value import PlayerType

# ---------------------------------------------------------------------------
# 1. Posture
# ---------------------------------------------------------------------------


class PostureMode(Enum):
    """Whether we contest the current matchup or write it off."""

    CONTEST = "contest"  # maximize P(win) for the current matchup
    PUNT = "punt"  # current week is decided, optimize next week


# `AggressionLevel` lives in models/plan.py. Its four *intensity* members are
# re-exported here so downstream packages import their whole vocabulary from
# this module. Intensity is orthogonal to `PostureMode`: you can punt
# conservatively or punt aggressively.
#
# `AggressionLevel.PREPARE` is deprecated: it is now `PostureMode.PUNT`. It
# is deliberately not re-exported and no package may emit it. P7 removes it
# from the enum once the legacy callers are gone.
CONSERVATIVE = AggressionLevel.CONSERVATIVE
NORMAL = AggressionLevel.NORMAL
AGGRESSIVE = AggressionLevel.AGGRESSIVE
DESPERATE = AggressionLevel.DESPERATE


@dataclass(frozen=True)
class Posture:
    """Where the seven-day window sits, and how deep we may reach for a move.

    Produced by P5. Read by P1 (window), P4 (floor), P6 (objective).
    """

    mode: PostureMode
    window_start: date  # inclusive
    window_end: date  # inclusive
    aggression: AggressionLevel  # sets the drop floor, nothing else
    p_win: float  # P(win) of the CURRENT matchup, populated even when punting
    importance: WeekImportance
    reasoning: tuple[str, ...]


# ---------------------------------------------------------------------------
# 2. League and roster state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeagueSettings:
    """League configuration that constrains what a plan may do."""

    league_key: str
    slots: RosterSlotSettings
    n_teams: int
    adds_per_week: int  # read/configured from Yahoo settings, never hardcoded
    waiver_days: int
    roster_size: int


@dataclass(frozen=True)
class RosterEntry:
    """One player currently on our roster."""

    nhl_id: int
    name: str
    team_abbrev: str
    positions: tuple[str, ...]  # Yahoo eligibility
    injury_status: str | None  # "IR", "IR+", "OUT", "DTD", None
    expected_return: date | None
    ir_eligible: bool  # can be moved to an IR slot right now
    is_protected: bool  # user-marked never-drop


@dataclass(frozen=True)
class ProjectionCache:
    """Every projection the planner will ever need, resolved once up front.

    The planner builds thousands of candidate grids. If each one called
    `forecast_player()` it would take hours, so `build_week_state` resolves
    the whole `(player, day)` cross-product before any planning starts and
    nothing below the planner touches the database or `src/predict/` again.

    A missing key means "no projection for that player on that day" — either
    the team does not play, or the player is expected to be out. It never
    means zero. Callers must distinguish the two.
    """

    values: Mapping[tuple[int, date], float]  # (nhl_id, game_date) -> expected FPTS

    def get(self, nhl_id: int, day: date) -> float | None:
        """Expected FPTS, or None if this player has no game we can value."""
        return self.values.get((nhl_id, day))

    def days_for(self, nhl_id: int) -> tuple[date, ...]:
        """Every day this player has a projection for, in order."""
        return tuple(sorted(d for (pid, d) in self.values if pid == nhl_id))


@dataclass(frozen=True)
class WeekState:
    """Immutable snapshot of the matchup and roster at a single `as_of`."""

    as_of: date
    week_start: date
    week_end: date
    league: LeagueSettings
    my_team_key: str
    opp_team_key: str
    roster: tuple[RosterEntry, ...]
    my_earned: float
    opp_earned: float
    adds_remaining: int
    opp_adds_remaining: int
    open_active_spots: int  # roster_size minus non-IR players
    open_ir_spots: int
    # NHL team abbreviations playing on each day, covering the planning window
    # and the seven terminal-value days after it. One query at state-build
    # time replaces the per-player-per-game session-per-call that made the
    # old heavy path slow.
    schedule: Mapping[date, frozenset[str]]
    projections: ProjectionCache


# ---------------------------------------------------------------------------
# 3. Lineup grid
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DaySlate:
    """One day of the window: who starts, who is blocked, what it is worth."""

    day: date
    starters: tuple[int, ...]  # nhl_ids assigned to active slots
    benched: tuple[int, ...]  # playing but slot-blocked
    open_slots: Mapping[str, int]  # "C" -> 1, "D" -> 0, ...
    mu: float  # sum of starter projections
    var: float  # sum of starter variances


class LineupGrid(Protocol):
    """Seven days of lineup assignments and the distribution they imply.

    P1 provides the implementation. P2, P4, and P6 type-hint against this
    protocol so they never import P1's internals.
    """

    window_start: date
    window_end: date
    days: tuple[DaySlate, ...]

    def mu(self) -> float:
        """Total projected FPTS across the window."""
        ...

    def sigma(self) -> float:
        """Standard deviation of total FPTS across the window."""
        ...

    def with_move(self, add: Candidate | None, drop: int | None, on_date: date) -> LineupGrid:
        """Return a new grid with the move applied from `on_date` forward.

        `add=None` means a pure drop. `drop=None` means a free add (open slot
        or IR). Days before `on_date` are unchanged. Never mutates: the
        planner explores thousands of branches and shared mutable state will
        corrupt them.
        """
        ...

    def marginal(self, candidate: Candidate, on_date: date) -> tuple[float, float]:
        """(delta_mu, delta_var) from inserting `candidate` at `on_date`, no drop."""
        ...


# ---------------------------------------------------------------------------
# 4. Candidates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """An acquirable player. Skaters and goalies compete in the same pool."""

    nhl_id: int
    name: str
    team_abbrev: str
    positions: tuple[str, ...]
    player_type: PlayerType
    # Expected FPTS per day, in-window only. For a goalie with a 60% chance of
    # starting this is 0.6 * start_value, already multiplied out. Days the
    # player does not play are absent, not present with a zero.
    projections: Mapping[date, float]
    terminal_value: float  # expected FPTS over the 7 days AFTER window_end
    available_from: date | None  # waiver clear date, None = available now
    upside_score: float  # [-1, 1]
    opportunity_score: float  # [-1, 1]
    # How sure the projections are, separate from the expectation itself. A
    # confirmed starter and a coin-flip starter can share a `projections` map
    # and differ entirely here. Feeds conviction in P6.
    confidence: float  # [0, 1]
    source: str  # "fa", "waiver", "goalie_stream"


# ---------------------------------------------------------------------------
# 5. Hold value and the floor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HoldValue:
    """What it costs us to give a rostered player away."""

    nhl_id: int
    ros_over_replacement: float  # dominant term
    window_contribution: float  # minor term
    upside_option: float
    scarcity: float
    total: float
    protected: bool  # hard never-drop, overrides everything
    reasoning: tuple[str, ...]


@dataclass(frozen=True)
class DropFloor:
    """The hard constraint aggression sets. Not a penalty, a gate."""

    aggression: AggressionLevel
    threshold: float

    def permits(self, hold: HoldValue) -> bool:
        """Whether a player this valuable may be dropped at all."""
        return not hold.protected and hold.total <= self.threshold


# ---------------------------------------------------------------------------
# 6. Plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedMove:
    """One dated transaction in the plan."""

    fire_date: date
    add: Candidate
    drop: RosterEntry | None  # None = free add via open slot or IR
    is_ir_move: bool  # move an injured player to IR, no drop
    delta_p_win: float
    delta_terminal: float
    alternates: tuple[Candidate, ...]  # ranked backups for this same slot
    reasoning: tuple[str, ...]


@dataclass(frozen=True)
class WeekPlan:
    """A dated sequence of moves for the rest of the window."""

    plan_id: str
    generated_at: datetime
    state_fingerprint: str  # hash of the WeekState that produced this
    posture: Posture
    moves: tuple[PlannedMove, ...]
    baseline_p_win: float  # P(win) if we do nothing
    projected_p_win: float  # P(win) if we execute every move
    adds_used: int
    conviction: float  # [0, 1], how sure we are in moves[0]
    fire_now: tuple[int, ...]  # indices into moves to execute this run
    reasoning: tuple[str, ...]


@dataclass(frozen=True)
class PlanSet:
    """The best plan plus whole-plan alternatives.

    Two levels of alternate, deliberately. `PlannedMove.alternates` answers
    "someone took my target, who else fits this slot." `PlanSet.alternates`
    answers "what is the second-best shape for the whole week."
    """

    best: WeekPlan
    alternates: tuple[WeekPlan, ...]  # ranked


# ---------------------------------------------------------------------------
# 7. Protocols
# ---------------------------------------------------------------------------


class ProjectionProvider(Protocol):
    """The only seam onto `src/predict/`.

    Exists so P1 can be tested with a fake and so nothing outside a single
    adapter imports the forecasting package directly.
    """

    def project(self, nhl_id: int, game_date: date) -> float:
        """Expected FPTS for this player on this date."""
        ...


class CandidateSource(Protocol):
    """Implemented by P2 (skaters) and P3 (goalies) so P6 sees one pool."""

    def candidates(self, state: WeekState, grid: LineupGrid, posture: Posture) -> list[Candidate]:
        """Acquirable players worth considering for this window."""
        ...


# ---------------------------------------------------------------------------
# 8. The variance signature
# ---------------------------------------------------------------------------
#
# Pinned here because P1 (skater variance) and P3 (goalie variance) are built
# in parallel and cannot agree on anything mid-flight. Neither may change
# these signatures.
#
# P1 owns `game_sigma` and its skater branch, in `src/optimize/week/variance.py`.
# P3 owns the goalie coefficients and hands them to P1's module as fitted
# constants. The grid calls `game_sigma`; `game_sigma` dispatches on
# `player_type` into the goalie path when needed.


class GameSigmaFn(Protocol):
    """Per-game standard deviation of fantasy points."""

    def __call__(self, projected_fpts: float, player_type: PlayerType) -> float: ...


class GoalieGameVarFn(Protocol):
    """Goalie per-game variance.

    A goalie has a second variance component a skater does not: start
    uncertainty is a Bernoulli term on top of outcome noise.
    """

    def __call__(self, p_start: float, start_value: float, outcome_var: float) -> float: ...

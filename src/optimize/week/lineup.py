"""LineupGrid — what our active lineup produces over a window, as a distribution.

This is the measuring instrument. Every package above it asks the same
question in different words: *what does my lineup produce over this window,
and how does that change if I make this move?*

For each day in the window the grid works out whose NHL team plays, assigns
them to active slots so as to maximize projected fantasy points, and records
the resulting `(mu, var)` plus which slots went unfilled. `with_move` produces
a new grid with an add/drop applied from a given date forward, recomputing
only the affected suffix, which is the difference between a beam search that
finishes and one that does not.

The grid measures. It does not judge: there is no notion of a "good" player
here, no ranking, and no scoring.

    grid = build_grid(state, window_start, window_end)
    grid.mu(), grid.sigma()
    better = grid.with_move(add=candidate, drop=8478402, on_date=thursday)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping, Sequence

from src.optimize.models import PlayerType, RosterPlayer, RosterSlotSettings
from src.optimize.models.week import Candidate, DaySlate, RosterEntry, WeekState
from src.optimize.slots import assign_players_to_slots
from src.optimize.week.state import TERMINAL_DAYS, days_between
from src.optimize.week.variance import game_sigma


@dataclass(frozen=True)
class Move:
    """One dated add/drop applied to a grid.

    `add=None` is a pure drop. `drop=None` is a free add against an open
    active slot or a freed IR slot. Both happen in real plans.
    """

    on_date: date
    add: Candidate | None
    drop: int | None


@dataclass(frozen=True)
class _Member:
    """A player present in the lineup pool, roster or acquired."""

    nhl_id: int
    name: str
    team_abbrev: str
    positions: tuple[str, ...]
    player_type: PlayerType
    candidate: Candidate | None  # None = rostered from the start
    available_from: date | None

    def as_roster_player(self) -> RosterPlayer:
        return RosterPlayer(
            name=self.name,
            team=self.team_abbrev,
            positions=list(self.positions),
            nhl_id=self.nhl_id,
        )


def _member_from_entry(entry: RosterEntry) -> _Member:
    return _Member(
        nhl_id=entry.nhl_id,
        name=entry.name,
        team_abbrev=entry.team_abbrev,
        positions=entry.positions,
        player_type=PlayerType.GOALIE if "G" in entry.positions else PlayerType.SKATER,
        candidate=None,
        available_from=None,
    )


def _member_from_candidate(candidate: Candidate) -> _Member:
    return _Member(
        nhl_id=candidate.nhl_id,
        name=candidate.name,
        team_abbrev=candidate.team_abbrev,
        positions=candidate.positions,
        player_type=candidate.player_type,
        candidate=candidate,
        available_from=candidate.available_from,
    )


class Grid:
    """Day-by-day lineup assignment over a window. Implements `LineupGrid`.

    Immutable by contract: `with_move` returns a new grid and days before the
    move date are reused by reference, never rebuilt and never mutated. The
    planner explores thousands of branches, and shared mutable state would
    corrupt all of them at once.
    """

    __slots__ = ("state", "window_start", "window_end", "moves", "days", "_slots")

    def __init__(
        self,
        state: WeekState,
        window_start: date,
        window_end: date,
        moves: tuple[Move, ...],
        days: tuple[DaySlate, ...],
    ):
        self.state = state
        self.window_start = window_start
        self.window_end = window_end
        self.moves = moves
        self.days = days
        self._slots = state.league.slots

    # -- distribution ----------------------------------------------------

    def mu(self) -> float:
        """Total projected FPTS across the window."""
        return sum(day.mu for day in self.days)

    def sigma(self) -> float:
        """Standard deviation of total FPTS across the window.

        Days and players are summed as independent variances. See
        `week/variance.py` for the measurement behind that assumption.
        """
        return sum(day.var for day in self.days) ** 0.5

    # -- what-ifs --------------------------------------------------------

    def with_move(
        self,
        add: Candidate | None,
        drop: int | None,
        on_date: date,
    ) -> "Grid":
        """A new grid with the move applied from `on_date` forward.

        A move on day D affects day D: Yahoo processes transactions
        immediately, so a Monday-morning add plays Monday night.
        """
        if add is None and drop is None:
            return self

        moves = self.moves + (Move(on_date=on_date, add=add, drop=drop),)
        # Days strictly before the earliest affected date are untouched, so
        # they carry over by reference.
        cutoff = max(on_date, self.window_start)
        kept = tuple(day for day in self.days if day.day < cutoff)
        rebuilt = _build_days(
            self.state, self._slots, moves,
            [d for d in days_between(self.window_start, self.window_end) if d >= cutoff],
        )
        return Grid(self.state, self.window_start, self.window_end, moves, kept + rebuilt)

    def marginal(self, candidate: Candidate, on_date: date) -> tuple[float, float]:
        """`(delta_mu, delta_var)` from inserting `candidate` at `on_date`, no drop."""
        after = self.with_move(add=candidate, drop=None, on_date=on_date)
        return (
            after.mu() - self.mu(),
            sum(d.var for d in after.days) - sum(d.var for d in self.days),
        )

    # -- introspection ---------------------------------------------------

    def day(self, when: date) -> DaySlate | None:
        """The slate for one day, or None if it falls outside the window."""
        for slate in self.days:
            if slate.day == when:
                return slate
        return None

    def roster_ids_on(self, when: date) -> frozenset[int]:
        """Who is on the roster on a given day, after applied moves."""
        return frozenset(m.nhl_id for m in _membership(self.state, self.moves, when).values())

    def __repr__(self) -> str:
        return (
            f"<Grid {self.window_start}..{self.window_end} "
            f"mu={self.mu():.1f} sigma={self.sigma():.1f} moves={len(self.moves)}>"
        )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def build_grid(state: WeekState, window_start: date, window_end: date) -> Grid:
    """Build the baseline grid: current roster, no moves, over this window."""
    days = days_between(window_start, window_end)
    slates = _build_days(state, state.league.slots, (), days)
    return Grid(state, window_start, window_end, (), slates)


def _membership(
    state: WeekState,
    moves: Sequence[Move],
    day: date,
) -> dict[int, _Member]:
    """Who is on the roster on `day`, after every move dated on or before it.

    Moves are folded in date order rather than application order, so a grid
    built by applying Thursday's move before Monday's still reads correctly.
    """
    members = {e.nhl_id: _member_from_entry(e) for e in state.roster}
    for move in sorted(moves, key=lambda m: m.on_date):
        if move.on_date > day:
            continue
        if move.drop is not None:
            members.pop(move.drop, None)
        if move.add is not None:
            members[move.add.nhl_id] = _member_from_candidate(move.add)
    return members


def _projection(state: WeekState, member: _Member, day: date) -> float | None:
    """Expected FPTS for this member on this day, or None if they do not play.

    Candidates carry their own projections (a goalie's is already multiplied
    by their chance of starting); rostered players come from the state cache.
    """
    if member.available_from is not None and day < member.available_from:
        return None
    if member.candidate is not None:
        return member.candidate.projections.get(day)
    return state.projections.get(member.nhl_id, day)


def _build_days(
    state: WeekState,
    slots: RosterSlotSettings,
    moves: Sequence[Move],
    days: Sequence[date],
) -> tuple[DaySlate, ...]:
    """Assign slots and score every day in `days`."""
    slate_counts = slots.active_slots()
    out = []

    for day in days:
        members = _membership(state, moves, day)

        # Only players with a projection are in the pool. A missing
        # projection means "no game we can value" — the team is idle, or the
        # player is expected to be out — so they cannot block a slot either.
        pool: dict[int, tuple[_Member, float]] = {}
        for member in members.values():
            projected = _projection(state, member, day)
            if projected is None:
                continue
            pool[member.nhl_id] = (member, projected)

        if not pool:
            out.append(
                DaySlate(
                    day=day,
                    starters=(),
                    benched=(),
                    open_slots=dict(slate_counts),
                    mu=0.0,
                    var=0.0,
                )
            )
            continue

        players = [m.as_roster_player() for m, _ in pool.values()]
        projections = {nhl_id: value for nhl_id, (_, value) in pool.items()}
        assignments = assign_players_to_slots(players, slots, projections=projections)

        starters: list[int] = []
        mu = 0.0
        var = 0.0
        used = {pos: 0 for pos in slate_counts}
        for pos, assigned in assignments.items():
            used[pos] = len(assigned)
            for player in assigned:
                member, projected = pool[player.nhl_id]
                starters.append(player.nhl_id)
                mu += projected
                var += game_sigma(projected, member.player_type) ** 2

        started = set(starters)
        benched = tuple(sorted(nhl_id for nhl_id in pool if nhl_id not in started))

        out.append(
            DaySlate(
                day=day,
                starters=tuple(sorted(starters)),
                benched=benched,
                open_slots={pos: slate_counts[pos] - used[pos] for pos in slate_counts},
                mu=mu,
                var=var,
            )
        )

    return tuple(out)


# ---------------------------------------------------------------------------
# Terminal value
# ---------------------------------------------------------------------------


def terminal_value(
    state: WeekState,
    nhl_id: int,
    window_end: date,
    days: int = TERMINAL_DAYS,
) -> float:
    """Expected FPTS over the seven days *after* the window closes.

    A scalar, not a grid: no slot check, no assignment, no variance. It is the
    rest-of-season term in the objective and it only breaks ties between moves
    whose `DELTA P(win)` is within epsilon of each other, so precision beyond
    one significant figure is wasted. Letting it outvote a live `DELTA P(win)`
    difference is the failure mode that refuses a one-day Sunday stream in a
    tight matchup because the alternative helps next week.
    """
    total = 0.0
    for offset in range(1, days + 1):
        value = state.projections.get(nhl_id, window_end + timedelta(days=offset))
        if value is not None:
            total += value
    return total


def window_projection(
    state: WeekState,
    nhl_id: int,
    window_start: date,
    window_end: date,
) -> Mapping[date, float]:
    """Per-day projections for one player inside a window.

    The shape `Candidate.projections` wants, straight from the cache: days
    the player does not play are absent, not present with a zero.
    """
    out = {}
    for day in days_between(window_start, window_end):
        value = state.projections.get(nhl_id, day)
        if value is not None:
            out[day] = value
    return out

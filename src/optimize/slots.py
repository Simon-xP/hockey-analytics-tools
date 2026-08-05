"""Daily lineup slot assignment.

Given the players whose NHL teams play on a date and the league's slot
configuration, work out who actually makes the active lineup. Multi-eligible
players (a C/LW can cover either) and the UTIL slot make this an assignment
problem, not a sort.

Two modes:

- **Unweighted** (default). Positional-scarcity greedy. Answers "can this
  player make the lineup at all," which is all the API roster view needs.
- **Weighted**. Pass `projections` and it solves a real max-weight bipartite
  matching over slot *instances* with `scipy.optimize.linear_sum_assignment`,
  maximizing projected fantasy points. This is what `week/lineup.py` uses,
  because a grid whose job is to compute expected points cannot afford to
  bench a 6-FPTS player in favour of a 2-FPTS one.

This is the "who plays today" half of the optimize layer — pure roster
mechanics, no valuation.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping, Optional

from sqlalchemy.orm import Session

from src.core.db import get_session
from src.core.models import Game, Team
from src.optimize.models import Roster, RosterPlayer, RosterSlotSettings


@dataclass
class DayAnalysis:
    """Analysis of roster slots for a single day."""

    date: date
    playing: list[RosterPlayer]
    assignments: dict[str, list[RosterPlayer]]  # Position -> assigned players
    slots_used: dict[str, int]
    slots_available: dict[str, int]
    can_stream: list[str]  # Positions with open slots

    def summary(self) -> str:
        """Return a one-line summary of the day."""
        playing_names = [p.name.split()[-1] for p in self.playing]  # Last names
        open_positions = ", ".join(self.can_stream) if self.can_stream else "none"
        return f"{self.date}: {len(self.playing)} playing ({', '.join(playing_names[:5])}{'...' if len(playing_names) > 5 else ''}) | Open: {open_positions}"


def get_teams_playing_on_date(
    target_date: date,
    session: Optional[Session] = None,
) -> set[str]:
    """Get set of team abbreviations playing on a given date.

    Pass `session` to reuse an open one. Called without it this opens (and
    closes) its own, which is fine for a one-off but ruinous in a loop — the
    weekly optimizer builds its whole schedule map in one query instead, via
    `src.optimize.week.state.build_schedule_map`.
    """
    if session is not None:
        return _teams_playing(session, target_date)
    with get_session() as owned:
        return _teams_playing(owned, target_date)


def _teams_playing(session: Session, target_date: date) -> set[str]:
    rows = (
        session.query(Team.abbrev)
        .join(
            Game,
            (Game.home_team_id == Team.team_id) | (Game.away_team_id == Team.team_id),
        )
        .filter(Game.date == target_date)
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def get_players_playing_on_date(roster: Roster, target_date: date) -> list[RosterPlayer]:
    """Get list of roster players whose teams play on a given date."""
    teams_playing = get_teams_playing_on_date(target_date)
    return [p for p in roster.players if p.team in teams_playing]


def player_key(player: RosterPlayer) -> object:
    """Stable identity for a roster player.

    `nhl_id` when we have it, object identity otherwise. Never the name: two
    players can share one (there are three active Sebastian Ahos' worth of
    precedent) and keying on it silently drops one of them from the lineup.
    """
    return player.nhl_id if player.nhl_id is not None else id(player)


def eligible_slots(player: RosterPlayer, slots: dict[str, int]) -> list[str]:
    """Slot names this player may legally occupy, given the league's slots."""
    if player.is_goalie():
        return ["G"] if slots.get("G", 0) > 0 else []

    out = [pos for pos in player.positions if pos != "G" and slots.get(pos, 0) > 0]
    if slots.get("UTIL", 0) > 0 and player.can_fill_util():
        out.append("UTIL")
    return out


def assign_players_to_slots(
    players: list[RosterPlayer],
    roster_slot_settings: RosterSlotSettings,
    projections: Optional[Mapping[int, float]] = None,
) -> dict[str, list[RosterPlayer]]:
    """Assign players to daily roster slots.

    Args:
        players: Players whose NHL team plays today.
        roster_slot_settings: League slot configuration.
        projections: Optional `nhl_id -> projected FPTS` map. When supplied,
            the assignment is a true max-weight bipartite matching that
            maximizes total projected points. When omitted, the cheaper
            positional-scarcity greedy runs and every player is worth the
            same, which is all a "who can play today" view needs.

    Returns:
        Dict mapping slot name -> list of assigned players.
    """
    if projections is not None:
        return _assign_weighted(players, roster_slot_settings, projections)
    return _assign_greedy(players, roster_slot_settings)


def _assign_weighted(
    players: list[RosterPlayer],
    roster_slot_settings: RosterSlotSettings,
    projections: Mapping[int, float],
) -> dict[str, list[RosterPlayer]]:
    """Max-weight assignment of players to individual slot instances.

    A league with 4 D slots gets 4 D columns, so the matching is over slot
    *instances* rather than slot types. Sizes are tiny (roughly 20 players by
    13 slots), so this is microseconds and can run thousands of times inside
    a beam search.
    """
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    slots = roster_slot_settings.active_slots()
    assignments: dict[str, list[RosterPlayer]] = {pos: [] for pos in slots}
    if not players:
        return assignments

    # One column per slot instance.
    columns = [pos for pos, count in slots.items() for _ in range(count)]
    if not columns:
        return assignments

    # Ineligible cells get a sentinel far below any real projection. The
    # matcher will still fill them when it runs out of legal pairings, so we
    # drop those assignments afterwards rather than trusting the matching.
    forbidden = -1e6
    weights = np.full((len(players), len(columns)), forbidden, dtype=float)

    for i, player in enumerate(players):
        allowed = set(eligible_slots(player, slots))
        if not allowed:
            continue
        value = float(projections.get(player.nhl_id, 0.0)) if player.nhl_id is not None else 0.0
        for j, pos in enumerate(columns):
            if pos in allowed:
                weights[i, j] = value

    rows, cols = linear_sum_assignment(weights, maximize=True)
    for i, j in zip(rows, cols):
        if weights[i, j] <= forbidden / 2:
            continue  # matcher padded an ineligible cell; not a real start
        assignments[columns[j]].append(players[i])

    for pos in assignments:
        assignments[pos].sort(
            key=lambda p: projections.get(p.nhl_id, 0.0) if p.nhl_id is not None else 0.0,
            reverse=True,
        )
    return assignments


def _assign_greedy(
    players: list[RosterPlayer],
    roster_slot_settings: RosterSlotSettings,
) -> dict[str, list[RosterPlayer]]:
    """Positional-scarcity greedy: single-position players pick first.

    Not optimal — that is what `_assign_weighted` is for — but it answers
    "does this player make the lineup" without needing projections.
    """
    slots = roster_slot_settings.active_slots()

    assignments: dict[str, list[RosterPlayer]] = {pos: [] for pos in slots}
    assigned: set[object] = set()

    goalies = [p for p in players if p.is_goalie()]
    skaters = [p for p in players if not p.is_goalie()]

    for goalie in goalies[: slots["G"]]:
        assignments["G"].append(goalie)
        assigned.add(player_key(goalie))

    # Single-position players first: they have the fewest ways to fit.
    skaters_sorted = sorted(skaters, key=lambda p: len(p.positions))

    for player in skaters_sorted:
        if player_key(player) in assigned:
            continue

        best_pos = None
        best_remaining = float("inf")
        for pos in player.positions:
            if pos == "G" or pos not in slots:
                continue
            remaining = slots[pos] - len(assignments[pos])
            if 0 < remaining < best_remaining:
                best_remaining = remaining
                best_pos = pos

        if best_pos:
            assignments[best_pos].append(player)
            assigned.add(player_key(player))

    util_slots = slots.get("UTIL", 0)
    for player in skaters_sorted:
        if player_key(player) in assigned:
            continue
        if len(assignments["UTIL"]) >= util_slots:
            break
        if player.can_fill_util():
            assignments["UTIL"].append(player)
            assigned.add(player_key(player))

    return assignments


def analyze_day(roster: Roster, target_date: date) -> DayAnalysis:
    """Analyze roster slot availability for a single day."""
    playing = get_players_playing_on_date(roster, target_date)
    assignments = assign_players_to_slots(playing, roster.roster_slot_settings)

    slots = roster.roster_slot_settings.active_slots()

    slots_used = {pos: len(assigned) for pos, assigned in assignments.items()}
    slots_available = {pos: slots[pos] - slots_used[pos] for pos in slots}

    # Positions where we can stream (have open slots, excluding UTIL for now)
    can_stream = [
        pos for pos, available in slots_available.items()
        if available > 0 and pos != "UTIL"
    ]

    # Add UTIL if there's room and we have space for a forward or D
    if slots_available.get("UTIL", 0) > 0:
        can_stream.append("UTIL")

    return DayAnalysis(
        date=target_date,
        playing=playing,
        assignments=assignments,
        slots_used=slots_used,
        slots_available=slots_available,
        can_stream=can_stream,
    )


def analyze_week(
    roster: Roster,
    yahoo_week: int,
) -> list[DayAnalysis]:
    """Analyze all days in a Yahoo fantasy week."""
    with get_session() as session:
        # Get date range for this week
        games = session.query(Game).filter(Game.yahoo_week == yahoo_week).all()
        if not games:
            return []

        dates = sorted(set(g.date for g in games))

    return [analyze_day(roster, d) for d in dates]


def analyze_date_range(
    roster: Roster,
    start_date: date,
    end_date: date,
) -> list[DayAnalysis]:
    """Analyze all days in a date range."""
    results = []
    current = start_date
    while current <= end_date:
        results.append(analyze_day(roster, current))
        current += timedelta(days=1)
    return results


def get_streaming_opportunities(
    roster: Roster,
    yahoo_week: int,
) -> dict[str, list[date]]:
    """
    Get days where each position can be streamed.

    Returns:
        Dict mapping position -> list of dates where that position is open
    """
    days = analyze_week(roster, yahoo_week)

    opportunities: dict[str, list[date]] = {
        "C": [], "LW": [], "RW": [], "D": [], "G": [], "UTIL": []
    }

    for day in days:
        for pos in day.can_stream:
            opportunities[pos].append(day.date)

    return opportunities

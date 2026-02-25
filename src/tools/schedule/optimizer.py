"""Schedule optimizer for fantasy hockey roster management."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from src.core.db import get_session
from src.core.models import Game, Team
from src.tools.schedule.models import Roster, RosterPlayer, LeagueSettings
from src.tools.schedule.config import load_roster


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


def get_teams_playing_on_date(target_date: date) -> set[str]:
    """Get set of team abbreviations playing on a given date."""
    with get_session() as session:
        games = session.query(Game).filter(Game.date == target_date).all()

        teams_playing = set()
        for game in games:
            home = session.query(Team).filter(Team.team_id == game.home_team_id).first()
            away = session.query(Team).filter(Team.team_id == game.away_team_id).first()
            if home:
                teams_playing.add(home.abbrev)
            if away:
                teams_playing.add(away.abbrev)

        return teams_playing


def get_players_playing_on_date(roster: Roster, target_date: date) -> list[RosterPlayer]:
    """Get list of roster players whose teams play on a given date."""
    teams_playing = get_teams_playing_on_date(target_date)
    return [p for p in roster.players if p.team in teams_playing]


def assign_players_to_slots(
    players: list[RosterPlayer],
    league_settings: LeagueSettings,
) -> dict[str, list[RosterPlayer]]:
    """
    Optimally assign players to roster slots using bipartite matching.

    Strategy:
    1. Separate goalies (they can only go in G slots)
    2. For skaters, use a priority-based greedy assignment:
       - First pass: assign single-position players
       - Second pass: assign multi-position players to scarcest slots
       - Third pass: fill UTIL with remaining players

    Returns:
        Dict mapping position -> list of assigned players
    """
    slots = league_settings.active_slots()

    assignments: dict[str, list[RosterPlayer]] = {pos: [] for pos in slots}
    assigned_players: set[str] = set()

    # Separate goalies and skaters
    goalies = [p for p in players if p.is_goalie()]
    skaters = [p for p in players if not p.is_goalie()]

    # Assign goalies (simple - they only fit in G)
    for goalie in goalies[:slots["G"]]:
        assignments["G"].append(goalie)
        assigned_players.add(goalie.name)

    # Sort skaters: single-position first, then by number of positions (ascending)
    skaters_sorted = sorted(skaters, key=lambda p: len(p.positions))

    # First pass: assign to primary positions (not UTIL)
    for player in skaters_sorted:
        if player.name in assigned_players:
            continue

        # Find available position with most scarcity
        best_pos = None
        best_scarcity = float("inf")

        for pos in player.positions:
            if pos in ["G"]:  # Skip goalie for skaters
                continue
            current = len(assignments[pos])
            max_slots = slots[pos]
            remaining = max_slots - current

            if remaining > 0:
                # Scarcity = how full is this position across all players?
                # Lower remaining = more scarce = higher priority
                if remaining < best_scarcity:
                    best_scarcity = remaining
                    best_pos = pos

        if best_pos:
            assignments[best_pos].append(player)
            assigned_players.add(player.name)

    # Second pass: fill UTIL with remaining skaters
    util_slots = slots.get("UTIL", 0)
    for player in skaters_sorted:
        if player.name in assigned_players:
            continue
        if len(assignments["UTIL"]) >= util_slots:
            break
        if player.can_fill_util():
            assignments["UTIL"].append(player)
            assigned_players.add(player.name)

    return assignments


def analyze_day(roster: Roster, target_date: date) -> DayAnalysis:
    """Analyze roster slot availability for a single day."""
    playing = get_players_playing_on_date(roster, target_date)
    assignments = assign_players_to_slots(playing, roster.league_settings)

    slots = roster.league_settings.active_slots()

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


def print_week_analysis(roster: Roster, yahoo_week: int) -> None:
    """Print a formatted analysis of a fantasy week."""
    days = analyze_week(roster, yahoo_week)

    if not days:
        print(f"No games found for week {yahoo_week}")
        return

    print(f"\n{'='*60}")
    print(f"WEEK {yahoo_week} ANALYSIS ({days[0].date} to {days[-1].date})")
    print(f"{'='*60}")
    print(f"League slots: {roster.league_settings.active_slots()}")
    print()

    for day in days:
        print(f"\n{day.date.strftime('%A %m/%d')}:")
        print(f"  Playing: {len(day.playing)} players")

        if day.playing:
            for pos in ["C", "LW", "RW", "D", "G", "UTIL"]:
                assigned = day.assignments.get(pos, [])
                max_slots = roster.league_settings.active_slots()[pos]
                names = [p.name.split()[-1] for p in assigned]
                status = "FULL" if len(assigned) >= max_slots else f"+{max_slots - len(assigned)}"
                print(f"    {pos}: {', '.join(names) if names else '-'} [{status}]")

        if day.can_stream:
            print(f"  >> Can stream: {', '.join(day.can_stream)}")
        else:
            print(f"  >> No open slots")


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


# Convenience function for quick analysis
def analyze(week: Optional[int] = None) -> None:
    """Quick analysis of current roster for a week."""
    roster = load_roster()
    if week is None:
        # Find current/next week with games
        with get_session() as session:
            today = date.today()
            game = session.query(Game).filter(Game.date >= today).order_by(Game.date).first()
            if game:
                week = game.yahoo_week

    if week:
        print_week_analysis(roster, week)
    else:
        print("No upcoming games found")

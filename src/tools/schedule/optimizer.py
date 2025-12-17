import pandas as pd
from pathlib import Path
from typing import List, Dict

DATA_DIR = Path(__file__).parents[3] / "data" / "raw"
schedule_df = pd.read_csv(DATA_DIR / "nhl-schedule-raw.csv")




# HELPER FUNCTIONS FROM GPT ================================

def _get_player_team(player_id: str) -> str:
    """
    Look up the team name for a player.

    Returns:
        Team name string (e.g., "TOR" for Toronto Maple Leafs)
    """
    pass


def _get_player_positions(player_id: str) -> List[str]:
    """
    Look up the list of positions a player is eligible for.

    Returns:
        List like ["C"], ["LW", "RW"], or ["C", "LW", "RW"].
    """
    pass


def _get_team_schedule(team_name: str) -> Dict[str, bool]:
    """
    Retrieve the weekly schedule for a given team.

    Returns:
        A dictionary mapping days of week to whether the team plays:
        {"MON": True, "TUE": False, ...}
    """
    pass


def _aggregate_daily_roster_activity(roster: List[str]) -> Dict[str, List[str]]:
    """
    Combine all player schedules into a daily list of active players.

    Returns:
        {
            "MON": ["player1", "player2", ...],
            "TUE": ["player3", ...],
            ...
        }
    """
    pass



# GPT MAIN FUNCTIONS ===========================================
def get_available_slots_by_day(
    roster: List[str],
    league_slots: Dict[str, int]
) -> Dict[str, Dict[str, bool]]:
    """
    Compute available position slots for each day of the week given a roster.

    Args:
        roster: List of player IDs or names currently on the team.
        league_slots: Dictionary defining the number of allowed players per position
                      (e.g., {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2, "UTIL": 2}).

    Returns:
        A dictionary like:
        {
            "MON": {"C": True, "LW": False, "RW": True, "D": True, "G": False},
            "TUE": {...},
            ...
        }

    Logic:
        1. Identify which players play each day (via their teams' schedules).
        2. For each day:
            - Count how many players with each position are active.
            - Compare to `league_slots` limits.
            - Mark `True` if an additional player could still be slotted that day.
        3. Exclude UTIL slots from the position dictionary output.
    """
    # Step 1: Aggregate player activity by day
    daily_activity = _aggregate_daily_roster_activity(roster)

    available_slots = {}
    for day, active_players in daily_activity.items():
        used_slots = {pos: 0 for pos in league_slots.keys()}
        for player_id in active_players:
            positions = _get_player_positions(player_id)
            # Simplify by assigning the first eligible position for counting
            # (could be optimized later for dual/triple-eligible logic)
            used_slots[positions[0]] += 1
        
        day_availability = {}
        for pos, max_slots in league_slots.items():
            if pos == "UTIL":
                continue
            day_availability[pos] = used_slots.get(pos, 0) < max_slots
        
        available_slots[day] = day_availability

    return available_slots


def is_position_available(
    position: str,
    roster: List[str],
    league_slots: Dict[str, int]
) -> Dict[str, bool]:
    """
    Simplified version for user queries.
    Returns True/False for each day indicating if a position slot is open.

    Args:
        position: The position to check ("C", "LW", "RW", "D", or "G").
        roster: List of player IDs or names.
        league_slots: Same structure as in get_available_slots_by_day().

    Returns:
        Example:
        {
            "MON": True,
            "TUE": False,
            "WED": True,
            ...
        }
    """
    full = get_available_slots_by_day(roster, league_slots)
    return {day: day_slots[position] for day, day_slots in full.items()}


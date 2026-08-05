"""Shared shift-data utilities.

Helpers for working with player shift data: time parsing, situation
classification, on-ice lookups, and data loading. Used by both the
correlation engine (correlate.py) and the RAPM model.
"""

from collections import defaultdict

from sqlalchemy import text


def time_to_seconds(time_str: str) -> int:
    """Convert 'MM:SS' to total seconds."""
    if not time_str:
        return 0
    parts = time_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def classify_situation(situation_code: str, team_id: int, home_team_id: int) -> str:
    """Classify a situation code into a bucket from a team's perspective.

    Args:
        situation_code: 4-char string, e.g. "1551".
            [0] = away goalies, [1] = away skaters,
            [2] = home skaters, [3] = home goalies
        team_id: The team we're classifying for.
        home_team_id: The home team in this game.

    Returns:
        "5v5", "4v4", "3v3", "pp", "pk", or "other".
    """
    if not situation_code or len(situation_code) != 4:
        return "other"

    away_goalies = int(situation_code[0])
    away_skaters = int(situation_code[1])
    home_skaters = int(situation_code[2])
    home_goalies = int(situation_code[3])

    is_home = (team_id == home_team_id)
    my_skaters = home_skaters if is_home else away_skaters
    opp_skaters = away_skaters if is_home else home_skaters
    my_goalies = home_goalies if is_home else away_goalies
    opp_goalies = away_goalies if is_home else home_goalies

    if my_goalies == 0 or opp_goalies == 0:
        return "other"

    if my_skaters == opp_skaters:
        if my_skaters == 5:
            return "5v5"
        elif my_skaters == 4:
            return "4v4"
        elif my_skaters == 3:
            return "3v3"
        else:
            return "other"

    if my_skaters > opp_skaters:
        return "pp"
    else:
        return "pk"


def load_events(session, game_id: int) -> list[dict]:
    """Load all events for a game, ordered by time."""
    rows = session.execute(
        text(
            "SELECT event_id, period, time_in_period, event_type, "
            "situation_code, x_coord, y_coord, zone_code, "
            "player_1_id, player_2_id, team_id, shot_type, sort_order, detail "
            "FROM game_events WHERE game_id = :gid ORDER BY sort_order"
        ),
        {"gid": game_id},
    ).fetchall()

    columns = [
        "event_id", "period", "time_in_period", "event_type",
        "situation_code", "x_coord", "y_coord", "zone_code",
        "player_1_id", "player_2_id", "team_id", "shot_type", "sort_order",
        "detail",
    ]
    return [dict(zip(columns, r)) for r in rows]


def load_shifts(session, game_id: int) -> list[dict]:
    """Load all shifts for a game."""
    rows = session.execute(
        text(
            "SELECT player_id, shift_number, period, start_time, end_time, "
            "duration, team_id FROM player_shifts WHERE game_id = :gid"
        ),
        {"gid": game_id},
    ).fetchall()

    columns = ["player_id", "shift_number", "period", "start_time", "end_time",
               "duration", "team_id"]
    return [dict(zip(columns, r)) for r in rows]


def load_shot_xg(session, game_id: int) -> dict[tuple[int, int], float]:
    """Load xG predictions for shot attempts in this game.

    Returns dict of (game_id, event_id) -> xg value.
    """
    rows = session.execute(
        text(
            "SELECT game_id, event_id, xg FROM shot_attempts "
            "WHERE game_id = :gid AND xg IS NOT NULL"
        ),
        {"gid": game_id},
    ).fetchall()

    return {(r[0], r[1]): r[2] for r in rows}


def build_situation_timeline(events: list[dict]) -> dict[int, list[tuple[int, str]]]:
    """Build a timeline of situation code changes per period.

    Returns: {period: [(time_seconds, situation_code), ...]} sorted by time.
    Each entry means "from this time onward, the situation is this code."
    """
    timeline = defaultdict(list)
    last_code = {}

    for e in events:
        period = e["period"]
        code = e["situation_code"]
        time_s = time_to_seconds(e["time_in_period"])

        if code and code != last_code.get(period):
            timeline[period].append((time_s, code))
            last_code[period] = code

    for period in timeline:
        timeline[period].sort()

    return dict(timeline)


def players_on_ice(
    shift_index: dict,
    team_id: int,
    period: int,
    event_time_s: int,
) -> set[int]:
    """Find all players on ice for a team at a given time.

    Uses a pre-built shift index for fast lookup.

    Args:
        shift_index: dict of (team_id, period) -> sorted list of
            (start_s, end_s, player_id) tuples.
        team_id: Team to query.
        period: Game period.
        event_time_s: Time in seconds (elapsed in period).
    """
    key = (team_id, period)
    if key not in shift_index:
        return set()

    on_ice = set()
    for start_s, end_s, player_id in shift_index[key]:
        if start_s <= event_time_s <= end_s:
            on_ice.add(player_id)
        elif start_s > event_time_s:
            break

    return on_ice


def build_shift_index(shifts: list[dict]) -> dict:
    """Build a time-indexed shift lookup from raw shift records.

    Returns: dict of (team_id, period) -> sorted list of
        (start_s, end_s, player_id) tuples.
    """
    shift_index = defaultdict(list)
    for s in shifts:
        start_s = time_to_seconds(s["start_time"])
        end_s = time_to_seconds(s["end_time"])
        if end_s <= start_s:
            continue
        shift_index[(s["team_id"], s["period"])].append(
            (start_s, end_s, s["player_id"])
        )

    for key in shift_index:
        shift_index[key].sort()

    return dict(shift_index)


def split_shift_by_situation(
    start_s: int,
    end_s: int,
    period: int,
    team_id: int,
    home_team_id: int,
    situation_timeline: dict,
) -> list[tuple[str, float]]:
    """Split a shift's TOI into situation segments.

    Returns list of (situation_name, seconds) for each situation that
    was active during this shift.
    """
    timeline = situation_timeline.get(period, [])

    if not timeline:
        return [("5v5", end_s - start_s)]

    segments = []
    current_time = start_s

    current_code = "1551"
    for t, code in timeline:
        if t <= start_s:
            current_code = code
        else:
            break

    for t, code in timeline:
        if t <= start_s:
            continue
        if t >= end_s:
            break

        seg_seconds = t - current_time
        if seg_seconds > 0:
            sit = classify_situation(current_code, team_id, home_team_id)
            segments.append((sit, seg_seconds))

        current_time = t
        current_code = code

    seg_seconds = end_s - current_time
    if seg_seconds > 0:
        sit = classify_situation(current_code, team_id, home_team_id)
        segments.append((sit, seg_seconds))

    return segments


def compute_attack_directions(events: list[dict]) -> dict[tuple[int, int], bool]:
    """Determine attacking direction per team per period from shot locations."""
    shot_xs = defaultdict(list)

    shot_types = {"shot-on-goal", "missed-shot", "blocked-shot", "goal"}
    for e in events:
        if e["event_type"] in shot_types and e["x_coord"] is not None:
            key = (e["team_id"], e["period"])
            shot_xs[key].append(e["x_coord"])

    directions = {}
    for (team_id, period), xs in shot_xs.items():
        if xs:
            avg_x = sum(xs) / len(xs)
            directions[(team_id, period)] = avg_x > 0

    return directions

import httpx
import time

BASE_URL = "https://api-web.nhle.com/v1"
STATS_URL = "https://api.nhle.com/stats/rest/en"
REQUEST_DELAY = 0.5  # seconds between requests to avoid rate limiting


def get_standings() -> dict:
    """Fetch current standings (includes all teams)."""
    response = httpx.get(f"{BASE_URL}/standings/now")
    response.raise_for_status()
    return response.json()


def get_team_roster(team_abbrev: str, season: str = "20252026") -> dict:
    """
    Fetch roster for a team.

    Uses season roster endpoint to include IR/LTIR players, not just active roster.
    """
    response = httpx.get(f"{BASE_URL}/roster/{team_abbrev}/{season}")
    response.raise_for_status()
    return response.json()


def get_all_teams() -> list[dict]:
    """
    Fetch all current NHL teams.

    Returns list of dicts with: team_id, abbrev, full_name, short_name
    """
    # Get team IDs from stats API
    response = httpx.get(f"{STATS_URL}/team")
    response.raise_for_status()
    all_teams = {t["triCode"]: t for t in response.json()["data"]}

    # Get current teams from standings
    standings = get_standings()
    teams = []

    for team_data in standings.get("standings", []):
        abbrev = team_data["teamAbbrev"]["default"]
        team_info = all_teams.get(abbrev, {})

        teams.append({
            "team_id": team_info.get("id"),
            "abbrev": abbrev,
            "full_name": team_data["teamName"]["default"],
            "short_name": team_data["teamCommonName"]["default"],
        })

    return teams


def get_all_players() -> list[dict]:
    """
    Fetch all players from all team rosters.

    Returns list of dicts with: nhl_id, full_name, team_abbrev, position
    """
    teams = get_all_teams()
    players = []

    for team in teams:
        abbrev = team["abbrev"]
        time.sleep(REQUEST_DELAY)
        try:
            roster = get_team_roster(abbrev)
        except httpx.HTTPStatusError as e:
            print(f"Warning: Could not fetch roster for {abbrev}: {e}")
            continue

        # Roster has forwards, defensemen, goalies sections
        for section in ["forwards", "defensemen", "goalies"]:
            for player in roster.get(section, []):
                players.append({
                    "nhl_id": player["id"],
                    "full_name": f"{player['firstName']['default']} {player['lastName']['default']}",
                    "team_abbrev": abbrev,
                    "position": player["positionCode"],
                })

    return players


def get_team_schedule(team_abbrev: str, season: str = "20252026") -> list[dict]:
    """
    Fetch a team's full season schedule with game results.

    Returns completed regular-season games only (no preseason/playoffs).
    Each dict has: game_id, game_date, home_team_id, away_team_id,
                   home_abbrev, away_abbrev, home_score, away_score
    """
    response = httpx.get(f"{BASE_URL}/club-schedule-season/{team_abbrev}/{season}")
    response.raise_for_status()
    data = response.json()

    games = []
    for g in data.get("games", []):
        # Regular season only (gameType 2)
        if g.get("gameType") != 2:
            continue
        # Only completed games
        if g.get("gameState") not in ("FINAL", "OFF"):
            continue

        games.append({
            "game_id": g["id"],
            "game_date": g["gameDate"],
            "home_team_id": g["homeTeam"]["id"],
            "away_team_id": g["awayTeam"]["id"],
            "home_abbrev": g["homeTeam"]["abbrev"],
            "away_abbrev": g["awayTeam"]["abbrev"],
            "home_score": g["homeTeam"]["score"],
            "away_score": g["awayTeam"]["score"],
        })

    return games


def get_game_play_by_play(game_id: int) -> list[dict]:
    """
    Fetch play-by-play events for a game.

    Returns list of dicts, one per event. Each event has:
      event_id, period, period_type, time_in_period, time_remaining,
      event_type, situation_code, sort_order,
      x_coord, y_coord, zone_code, shot_type,
      player_1_id, player_2_id, team_id, detail
    """
    time.sleep(REQUEST_DELAY)
    response = httpx.get(f"{BASE_URL}/gamecenter/{game_id}/play-by-play")
    response.raise_for_status()
    data = response.json()

    home_team_id = data.get("homeTeam", {}).get("id")
    away_team_id = data.get("awayTeam", {}).get("id")

    events = []
    for play in data.get("plays", []):
        details = play.get("details", {})
        event_type = play.get("typeDescKey", "")

        # Determine primary and secondary player based on event type
        player_1_id = None
        player_2_id = None

        if event_type in ("shot-on-goal", "missed-shot"):
            player_1_id = details.get("shootingPlayerId")
            player_2_id = details.get("goalieInNetId")
        elif event_type == "goal":
            player_1_id = details.get("scoringPlayerId")
            player_2_id = details.get("goalieInNetId")
        elif event_type == "blocked-shot":
            player_1_id = details.get("shootingPlayerId")
            player_2_id = details.get("blockingPlayerId")
        elif event_type == "hit":
            player_1_id = details.get("hittingPlayerId")
            player_2_id = details.get("hitteePlayerId")
        elif event_type == "faceoff":
            player_1_id = details.get("winningPlayerId")
            player_2_id = details.get("losingPlayerId")
        elif event_type in ("giveaway", "takeaway"):
            player_1_id = details.get("playerId")
        elif event_type == "penalty":
            player_1_id = details.get("committedByPlayerId")
            player_2_id = details.get("drawnByPlayerId")

        period_desc = play.get("periodDescriptor", {})

        events.append({
            "event_id": play.get("eventId"),
            "period": period_desc.get("number"),
            "period_type": period_desc.get("periodType"),
            "time_in_period": play.get("timeInPeriod"),
            "time_remaining": play.get("timeRemaining"),
            "event_type": event_type,
            "situation_code": play.get("situationCode"),
            "sort_order": play.get("sortOrder"),
            "x_coord": details.get("xCoord"),
            "y_coord": details.get("yCoord"),
            "zone_code": details.get("zoneCode"),
            "shot_type": details.get("shotType"),
            "player_1_id": player_1_id,
            "player_2_id": player_2_id,
            "team_id": details.get("eventOwnerTeamId"),
            "detail": details,
        })

    return events


def get_game_boxscore(game_id: int) -> dict:
    """Fetch boxscore JSON for a game (used to map sweater -> player_id)."""
    time.sleep(REQUEST_DELAY)
    response = httpx.get(f"{BASE_URL}/gamecenter/{game_id}/boxscore")
    response.raise_for_status()
    return response.json()


def get_game_shifts(game_id: int) -> list[dict]:
    """
    Fetch shift chart data for a game.

    Returns list of dicts, one per shift. Each shift has:
      player_id, shift_number, period, start_time, end_time, duration, team_id

    The NHL stats `shiftcharts` endpoint randomly returns empty data for
    ~38% of recent games. When that happens, fall back to parsing the
    official HTML shift reports.
    """
    time.sleep(REQUEST_DELAY)
    url = f"{STATS_URL}/shiftcharts?cayenneExp=gameId={game_id}"
    response = httpx.get(url)
    response.raise_for_status()
    data = response.json()

    shifts = []
    for s in data.get("data", []):
        # Skip non-shift entries (goals/events embedded in shift chart have
        # shiftNumber=0 and null duration) and shifts with missing timing
        if not s.get("shiftNumber") or not s.get("duration"):
            continue
        if not s.get("startTime") or not s.get("endTime"):
            continue

        shifts.append({
            "player_id": s.get("playerId"),
            "shift_number": s.get("shiftNumber"),
            "period": s.get("period"),
            "start_time": s.get("startTime"),
            "end_time": s.get("endTime"),
            "duration": s.get("duration"),
            "team_id": s.get("teamId"),
        })

    if not shifts:
        from src.ingest.nhl_api.html_shifts import get_game_shifts_from_html
        shifts = get_game_shifts_from_html(game_id)

    return shifts


def get_completed_games(date_str: str) -> list[int]:
    """
    Fetch game IDs for all completed games on a given date.

    Args:
        date_str: Date in YYYY-MM-DD format.

    Returns list of game IDs (integers).
    """
    time.sleep(REQUEST_DELAY)
    response = httpx.get(f"{BASE_URL}/schedule/{date_str}")
    response.raise_for_status()
    data = response.json()

    game_ids = []
    for week in data.get("gameWeek", []):
        if week.get("date") != date_str:
            continue
        for game in week.get("games", []):
            if game.get("gameState") in ("FINAL", "OFF"):
                game_ids.append(game["id"])

    return game_ids


def get_skaters_with_games(season: str = "20252026") -> list[dict]:
    """
    Fetch all skaters who have played at least one game in the season.

    This catches players not on current rosters (AHL callups, etc.).
    Returns list of dicts with: nhl_id, full_name, team_abbrev, position
    """
    url = f"{STATS_URL}/skater/summary?cayenneExp=seasonId={season}%20and%20gameTypeId=2&limit=-1"
    response = httpx.get(url)
    response.raise_for_status()
    data = response.json()

    players = []
    for p in data.get("data", []):
        # Map position codes
        pos_code = p.get("positionCode", "")
        if pos_code in ("L", "R", "C"):
            position = pos_code
        elif pos_code == "D":
            position = "D"
        else:
            position = "F"

        players.append({
            "nhl_id": p.get("playerId"),
            "full_name": p.get("skaterFullName"),
            "team_abbrev": p.get("teamAbbrevs"),  # Can be comma-separated
            "position": position,
        })

    return players

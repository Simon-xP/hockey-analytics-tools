import requests
import time

BASE_URL = "https://api-web.nhle.com/v1"
STATS_URL = "https://api.nhle.com/stats/rest/en"
REQUEST_DELAY = 0.5  # seconds between requests to avoid rate limiting


def get_standings() -> dict:
    """Fetch current standings (includes all teams)."""
    response = requests.get(f"{BASE_URL}/standings/now")
    response.raise_for_status()
    return response.json()


def get_team_roster(team_abbrev: str, season: str = "20252026") -> dict:
    """
    Fetch roster for a team.

    Uses season roster endpoint to include IR/LTIR players, not just active roster.
    """
    response = requests.get(f"{BASE_URL}/roster/{team_abbrev}/{season}")
    response.raise_for_status()
    return response.json()


def get_all_teams() -> list[dict]:
    """
    Fetch all current NHL teams.

    Returns list of dicts with: team_id, abbrev, full_name, short_name
    """
    # Get team IDs from stats API
    response = requests.get(f"{STATS_URL}/team")
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
        except requests.HTTPError as e:
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
    response = requests.get(f"{BASE_URL}/club-schedule-season/{team_abbrev}/{season}")
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


def get_skaters_with_games(season: str = "20252026") -> list[dict]:
    """
    Fetch all skaters who have played at least one game in the season.

    This catches players not on current rosters (AHL callups, etc.).
    Returns list of dicts with: nhl_id, full_name, team_abbrev, position
    """
    url = f"{STATS_URL}/skater/summary?cayenneExp=seasonId={season}%20and%20gameTypeId=2&limit=-1"
    response = requests.get(url)
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

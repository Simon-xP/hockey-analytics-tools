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


def get_team_roster(team_abbrev: str) -> dict:
    """Fetch current roster for a team."""
    response = requests.get(f"{BASE_URL}/roster/{team_abbrev}/current")
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

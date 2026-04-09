"""Yahoo Fantasy Sports API client.

Wraps the Yahoo Fantasy API for NHL. All methods require authentication
via auth.py first.

API docs: https://developer.yahoo.com/fantasysports/guide/
Base URL: https://fantasysports.yahooapis.com/fantasy/v2
"""

import requests
import xml.etree.ElementTree as ET

from src.ingest.yahoo.auth import get_access_token

BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"

# NHL game key changes each season. "nhl" resolves to current season.
GAME_KEY = "nhl"

NS = {"yh": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}


def _get(path: str, params: dict = None) -> ET.Element:
    """Make an authenticated GET request to the Yahoo Fantasy API.

    Returns parsed XML root element.
    """
    token = get_access_token()
    if not token:
        raise ValueError("Not authenticated with Yahoo. Connect your account first.")

    url = f"{BASE_URL}{path}"
    resp = requests.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def _text(el: ET.Element, tag: str) -> str | None:
    """Get text content of a child element."""
    child = el.find(f"yh:{tag}", NS)
    return child.text if child is not None else None


def get_user_leagues() -> list[dict]:
    """Get all NHL fantasy leagues for the authenticated user."""
    root = _get(f"/users;use_login=1/games;game_keys={GAME_KEY}/leagues")

    leagues = []
    for league_el in root.iter(f"{{{NS['yh']}}}league"):
        leagues.append({
            "league_key": _text(league_el, "league_key"),
            "league_id": _text(league_el, "league_id"),
            "name": _text(league_el, "name"),
            "num_teams": _text(league_el, "num_teams"),
            "season": _text(league_el, "season"),
            "scoring_type": _text(league_el, "scoring_type"),
        })

    return leagues


def get_my_team(league_key: str) -> dict:
    """Get the authenticated user's team in a league."""
    root = _get(f"/league/{league_key}/teams;out=roster")

    # Find user's team (is_owned_by_current_login)
    for team_el in root.iter(f"{{{NS['yh']}}}team"):
        is_mine = _text(team_el, "is_owned_by_current_login")
        if is_mine == "1":
            return _parse_team(team_el)

    return {}


def get_league_standings(league_key: str) -> list[dict]:
    """Get league standings."""
    root = _get(f"/league/{league_key}/standings")

    standings = []
    for team_el in root.iter(f"{{{NS['yh']}}}team"):
        standing = {
            "team_key": _text(team_el, "team_key"),
            "name": _text(team_el, "name"),
        }

        standings_el = team_el.find(f"yh:team_standings", NS)
        if standings_el is not None:
            standing["rank"] = _text(standings_el, "rank")
            outcome = standings_el.find(f"yh:outcome_totals", NS)
            if outcome is not None:
                standing["wins"] = _text(outcome, "wins")
                standing["losses"] = _text(outcome, "losses")
                standing["ties"] = _text(outcome, "ties")
                standing["points_for"] = _text(outcome, "points_for")
                standing["points_against"] = _text(outcome, "points_against")

        standings.append(standing)

    standings.sort(key=lambda x: int(x.get("rank", 99)))
    return standings


def get_matchup(league_key: str, week: int = None) -> dict:
    """Get the authenticated user's current matchup."""
    path = f"/league/{league_key}/teams;out=roster"
    if week:
        path += f";week={week}"

    # Get my team key first
    my_team = get_my_team(league_key)
    if not my_team:
        return {}

    # Get matchups
    week_param = f";weeks={week}" if week else ""
    root = _get(f"/team/{my_team['team_key']}/matchups{week_param}")

    matchups = []
    for matchup_el in root.iter(f"{{{NS['yh']}}}matchup"):
        week_num = _text(matchup_el, "week")
        teams = []
        for team_el in matchup_el.iter(f"{{{NS['yh']}}}team"):
            teams.append({
                "team_key": _text(team_el, "team_key"),
                "name": _text(team_el, "name"),
            })
        matchups.append({"week": week_num, "teams": teams})

    return matchups[0] if matchups else {}


def get_free_agents(league_key: str, position: str = None, count: int = 25) -> list[dict]:
    """Get available free agents in a league."""
    params = {"count": count, "status": "FA"}
    if position:
        params["position"] = position

    root = _get(f"/league/{league_key}/players;status=FA;count={count}")

    players = []
    for player_el in root.iter(f"{{{NS['yh']}}}player"):
        players.append({
            "player_key": _text(player_el, "player_key"),
            "player_id": _text(player_el, "player_id"),
            "name": _text(player_el.find(f"yh:name", NS), "full") if player_el.find(f"yh:name", NS) is not None else None,
            "team": _text(player_el, "editorial_team_abbr"),
            "position": _text(player_el, "display_position"),
            "status": _text(player_el, "status"),
        })

    return players


def get_trending_players(league_key: str, count: int = 20) -> list[dict]:
    """Get players sorted by ownership rank with ownership delta.

    Shows the most-transacted players with their week-over-week ownership change.
    """
    root = _get(f"/league/{league_key}/players;status=ALL;sort=AR;out=percent_owned;count={count}")

    players = []
    for player_el in root.iter(f"{{{NS['yh']}}}player"):
        pct_el = player_el.find(f"yh:percent_owned", NS)
        pct_value = None
        pct_delta = None
        if pct_el is not None:
            val = _text(pct_el, "value")
            delta = _text(pct_el, "delta")
            pct_value = int(val) if val else None
            pct_delta = int(delta) if delta else None

        players.append({
            "player_key": _text(player_el, "player_key"),
            "player_id": _text(player_el, "player_id"),
            "name": _text(player_el.find(f"yh:name", NS), "full") if player_el.find(f"yh:name", NS) is not None else None,
            "team": _text(player_el, "editorial_team_abbr"),
            "position": _text(player_el, "display_position"),
            "status": _text(player_el, "status"),
            "percent_owned": pct_value,
            "ownership_delta": pct_delta,
        })

    return players


def _parse_team(team_el: ET.Element) -> dict:
    """Parse a team element into a dict."""
    team = {
        "team_key": _text(team_el, "team_key"),
        "team_id": _text(team_el, "team_id"),
        "name": _text(team_el, "name"),
    }

    # Parse roster
    roster = []
    for player_el in team_el.iter(f"{{{NS['yh']}}}player"):
        player = {
            "player_key": _text(player_el, "player_key"),
            "player_id": _text(player_el, "player_id"),
            "name": _text(player_el.find(f"yh:name", NS), "full") if player_el.find(f"yh:name", NS) is not None else None,
            "team": _text(player_el, "editorial_team_abbr"),
            "position": _text(player_el, "display_position"),
            "status": _text(player_el, "status"),
        }

        # Selected position (what slot they're in)
        sel_pos = player_el.find(f"yh:selected_position", NS)
        if sel_pos is not None:
            player["selected_position"] = _text(sel_pos, "position")

        roster.append(player)

    team["roster"] = roster
    return team

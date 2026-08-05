"""Yahoo Fantasy Sports API client.

Wraps the Yahoo Fantasy API for NHL. All methods require authentication
via auth.py first.

API docs: https://developer.yahoo.com/fantasysports/guide/
Base URL: https://fantasysports.yahooapis.com/fantasy/v2
"""

import xml.etree.ElementTree as ET

import httpx

from src.ingest.yahoo.auth import get_access_token

BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"

# NHL game key changes each season. "nhl" resolves to current season.
GAME_KEY = "nhl"

NS = {"yh": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}


def _get(path: str, params: dict = None) -> ET.Element:
    """Make an authenticated GET request to the Yahoo Fantasy API."""
    token = get_access_token()
    if not token:
        raise ValueError("Not authenticated with Yahoo. Connect your account first.")

    url = f"{BASE_URL}{path}"
    resp = httpx.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def _put(path: str, xml_body: str) -> ET.Element:
    """Make an authenticated PUT request with an XML body."""
    token = get_access_token()
    if not token:
        raise ValueError("Not authenticated with Yahoo. Connect your account first.")

    url = f"{BASE_URL}{path}"
    resp = httpx.put(
        url,
        content=xml_body.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/xml",
        },
    )
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def _post(path: str, xml_body: str) -> ET.Element:
    """Make an authenticated POST request with an XML body."""
    token = get_access_token()
    if not token:
        raise ValueError("Not authenticated with Yahoo. Connect your account first.")

    url = f"{BASE_URL}{path}"
    resp = httpx.post(
        url,
        content=xml_body.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/xml",
        },
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

        standings_el = team_el.find("yh:team_standings", NS)
        if standings_el is not None:
            standing["rank"] = _text(standings_el, "rank")
            outcome = standings_el.find("yh:outcome_totals", NS)
            if outcome is not None:
                standing["wins"] = _text(outcome, "wins")
                standing["losses"] = _text(outcome, "losses")
                standing["ties"] = _text(outcome, "ties")
                standing["points_for"] = _text(outcome, "points_for")
                standing["points_against"] = _text(outcome, "points_against")

        standings.append(standing)

    standings.sort(key=lambda x: int(x.get("rank", 99)))
    return standings


def get_league_settings(league_key: str) -> dict:
    """Get a league's roster, waiver, and transaction-limit settings.

    Returns a dict with `n_teams`, `adds_per_week`, `waiver_days`,
    `roster_size`, and `roster_positions` (slot name -> count). Values Yahoo
    does not report are absent rather than defaulted — the caller decides
    what to substitute and is expected to say so out loud.
    """
    root = _get(f"/league/{league_key}/settings")

    settings: dict = {}

    league_el = next(root.iter(f"{{{NS['yh']}}}league"), None)
    if league_el is not None:
        num_teams = _text(league_el, "num_teams")
        if num_teams:
            settings["n_teams"] = int(num_teams)

    settings_el = next(root.iter(f"{{{NS['yh']}}}settings"), None)
    if settings_el is None:
        return settings

    waiver_time = _text(settings_el, "waiver_time")
    if waiver_time:
        settings["waiver_days"] = int(waiver_time)

    # Yahoo reports the *weekly* cap as max_weekly_adds and a season cap as
    # max_adds. Only the weekly one constrains a week plan.
    weekly_adds = _text(settings_el, "max_weekly_adds")
    if weekly_adds:
        settings["adds_per_week"] = int(weekly_adds)

    positions: dict[str, int] = {}
    for pos_el in settings_el.iter(f"{{{NS['yh']}}}roster_position"):
        name = _text(pos_el, "position")
        count = _text(pos_el, "count")
        if name and count:
            positions[name] = int(count)
    if positions:
        settings["roster_positions"] = positions
        # Roster size is everything but IR: those slots hold players who
        # cannot be started and do not count against the active limit.
        settings["roster_size"] = sum(
            count for name, count in positions.items() if not name.startswith("IR")
        )

    return settings


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


def get_free_agents(league_key: str, count: int = 50, sort: str = "AR") -> list[dict]:
    """Get available free agents in a league, sorted by average rank.

    Paginates through Yahoo's 25-per-page limit to get more results.
    sort=AR gives the most relevant active players.
    """
    players = []
    start = 0

    while len(players) < count:
        batch = min(25, count - len(players))
        root = _get(
            f"/league/{league_key}/players;status=FA;sort={sort}"
            f";start={start};count={batch}"
        )

        page_players = []
        for player_el in root.iter(f"{{{NS['yh']}}}player"):
            page_players.append({
                "player_key": _text(player_el, "player_key"),
                "player_id": _text(player_el, "player_id"),
                "name": _text(player_el.find("yh:name", NS), "full") if player_el.find("yh:name", NS) is not None else None,
                "team": _text(player_el, "editorial_team_abbr"),
                "position": _text(player_el, "display_position"),
                "status": _text(player_el, "status"),
            })

        if not page_players:
            break

        players.extend(page_players)
        start += len(page_players)

    return players


def get_trending_players(league_key: str, count: int = 20) -> list[dict]:
    """Get players sorted by ownership rank with ownership delta.

    Shows the most-transacted players with their week-over-week ownership change.
    """
    root = _get(f"/league/{league_key}/players;status=ALL;sort=AR;out=percent_owned;count={count}")

    players = []
    for player_el in root.iter(f"{{{NS['yh']}}}player"):
        pct_el = player_el.find("yh:percent_owned", NS)
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
            "name": _text(player_el.find("yh:name", NS), "full") if player_el.find("yh:name", NS) is not None else None,
            "team": _text(player_el, "editorial_team_abbr"),
            "position": _text(player_el, "display_position"),
            "status": _text(player_el, "status"),
            "percent_owned": pct_value,
            "ownership_delta": pct_delta,
        })

    return players


def get_scoreboard(league_key: str, week: int = None) -> list[dict]:
    """Get scoreboard with earned and projected scores for all matchups.

    TODO: XML parsing is untested -- written from Yahoo API docs, not a real
    response. Needs verification against a live scoreboard during the season.
    """
    week_param = f";week={week}" if week else ""
    root = _get(f"/league/{league_key}/scoreboard{week_param}")

    matchups = []
    for matchup_el in root.iter(f"{{{NS['yh']}}}matchup"):
        week_num = _text(matchup_el, "week")
        week_start = _text(matchup_el, "week_start")
        week_end = _text(matchup_el, "week_end")

        teams = []
        for team_el in matchup_el.iter(f"{{{NS['yh']}}}team"):
            team = {
                "team_key": _text(team_el, "team_key"),
                "name": _text(team_el, "name"),
                "is_owned_by_current_login": _text(team_el, "is_owned_by_current_login") == "1",
            }

            points_el = team_el.find("yh:team_points", NS)
            if points_el is not None:
                total = _text(points_el, "total")
                team["points"] = float(total) if total else 0.0

            projected_el = team_el.find("yh:team_projected_points", NS)
            if projected_el is not None:
                total = _text(projected_el, "total")
                team["projected_points"] = float(total) if total else 0.0

            teams.append(team)

        matchups.append({
            "week": int(week_num) if week_num else None,
            "week_start": week_start,
            "week_end": week_end,
            "teams": teams,
        })

    return matchups


def get_all_rosters(league_key: str) -> list[dict]:
    """Get current rosters for every team in the league."""
    root = _get(f"/league/{league_key}/teams;out=roster")

    teams = []
    for team_el in root.iter(f"{{{NS['yh']}}}team"):
        teams.append(_parse_team(team_el))
    return teams


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
            "name": _text(player_el.find("yh:name", NS), "full") if player_el.find("yh:name", NS) is not None else None,
            "team": _text(player_el, "editorial_team_abbr"),
            "position": _text(player_el, "display_position"),
            "status": _text(player_el, "status"),
        }

        # Selected position (what slot they're in)
        sel_pos = player_el.find("yh:selected_position", NS)
        if sel_pos is not None:
            player["selected_position"] = _text(sel_pos, "position")

        roster.append(player)

    team["roster"] = roster
    return team


# --- Write operations ---


def add_drop_player(
    league_key: str,
    team_key: str,
    add_player_key: str,
    drop_player_key: str | None = None,
) -> dict:
    """Add a free agent to roster, optionally dropping a player.

    If drop_player_key is None, adds to an empty roster slot.
    Returns the transaction confirmation from Yahoo.
    """
    if drop_player_key:
        xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<fantasy_content>
  <transaction>
    <type>add/drop</type>
    <players>
      <player>
        <player_key>{add_player_key}</player_key>
        <transaction_data>
          <type>add</type>
          <destination_team_key>{team_key}</destination_team_key>
        </transaction_data>
      </player>
      <player>
        <player_key>{drop_player_key}</player_key>
        <transaction_data>
          <type>drop</type>
          <source_team_key>{team_key}</source_team_key>
        </transaction_data>
      </player>
    </players>
  </transaction>
</fantasy_content>"""
    else:
        xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<fantasy_content>
  <transaction>
    <type>add</type>
    <players>
      <player>
        <player_key>{add_player_key}</player_key>
        <transaction_data>
          <type>add</type>
          <destination_team_key>{team_key}</destination_team_key>
        </transaction_data>
      </player>
    </players>
  </transaction>
</fantasy_content>"""

    root = _post(f"/league/{league_key}/transactions", xml_body)
    txn_el = root.find(f".//{{{NS['yh']}}}transaction")
    if txn_el is not None:
        return {
            "transaction_key": _text(txn_el, "transaction_key"),
            "type": _text(txn_el, "type"),
            "status": _text(txn_el, "status"),
        }
    return {"status": "submitted"}


def set_player_position(
    team_key: str,
    player_key: str,
    position: str,
    coverage_date: str,
) -> dict:
    """Move a player to a specific roster slot for a given date.

    position: "C", "LW", "RW", "D", "G", "UTIL", "BN", "IR", "IR+"
    coverage_date: "YYYY-MM-DD"
    """
    xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<fantasy_content>
  <roster>
    <coverage_type>date</coverage_type>
    <date>{coverage_date}</date>
    <players>
      <player>
        <player_key>{player_key}</player_key>
        <position>{position}</position>
      </player>
    </players>
  </roster>
</fantasy_content>"""

    root = _put(f"/team/{team_key}/roster", xml_body)
    return {"status": "ok", "player_key": player_key, "position": position}


def set_lineup(
    team_key: str,
    moves: list[dict],
    coverage_date: str,
) -> dict:
    """Set multiple player positions for a given date.

    moves: list of {"player_key": "...", "position": "..."} dicts
    coverage_date: "YYYY-MM-DD"
    """
    players_xml = ""
    for move in moves:
        players_xml += f"""
      <player>
        <player_key>{move['player_key']}</player_key>
        <position>{move['position']}</position>
      </player>"""

    xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<fantasy_content>
  <roster>
    <coverage_type>date</coverage_type>
    <date>{coverage_date}</date>
    <players>{players_xml}
    </players>
  </roster>
</fantasy_content>"""

    root = _put(f"/team/{team_key}/roster", xml_body)
    return {
        "status": "ok",
        "date": coverage_date,
        "moves": len(moves),
    }


def move_to_ir(
    team_key: str,
    player_key: str,
    ir_type: str = "IR",
    coverage_date: str | None = None,
) -> dict:
    """Place a player on IR or IR+.

    ir_type: "IR" or "IR+" — player must have matching Yahoo status to be eligible.
    """
    from datetime import date as date_type

    if coverage_date is None:
        coverage_date = str(date_type.today())

    return set_player_position(team_key, player_key, ir_type, coverage_date)


def activate_from_ir(
    team_key: str,
    player_key: str,
    position: str,
    coverage_date: str | None = None,
) -> dict:
    """Move a player off IR back to an active roster slot.

    position: the active slot to place them in ("C", "LW", "RW", "D", "G", "UTIL", "BN")
    """
    from datetime import date as date_type

    if coverage_date is None:
        coverage_date = str(date_type.today())

    return set_player_position(team_key, player_key, position, coverage_date)

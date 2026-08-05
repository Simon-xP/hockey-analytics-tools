"""Sync Yahoo Fantasy player ID mapping to our DB.

For backtesting data (draft picks, transactions, roster reconstruction),
see src/ingest/yahoo/league_sync.py.
"""

import time
import xml.etree.ElementTree as ET

import httpx

from src.core.db import get_session
from src.core.models import Player, TeamRoster
from src.core.resolver import resolve_player
from src.ingest.yahoo.auth import get_access_token
from src.ingest.yahoo.client import get_all_rosters

NS = {"yh": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}


def sync_yahoo_players(league_key: str) -> dict:
    """Fetch all Yahoo players and map to our DB.

    Returns dict with counts: matched, unmatched, total.
    """
    token = get_access_token()
    if not token:
        raise ValueError("Not authenticated with Yahoo")

    all_yahoo = []
    start = 0
    while True:
        url = (
            f"https://fantasysports.yahooapis.com/fantasy/v2"
            f"/league/{league_key}/players"
            f";status=ALL;start={start};count=25"
        )
        resp = httpx.get(url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 200:
            break

        root = ET.fromstring(resp.content)
        page = list(root.iter(f"{{{NS['yh']}}}player"))
        if not page:
            break

        for el in page:
            name_el = el.find(f"yh:name", NS)
            all_yahoo.append({
                "yahoo_id": int(el.findtext(f"yh:player_id", namespaces=NS) or 0),
                "name": name_el.findtext(f"yh:full", namespaces=NS) if name_el else None,
                "positions": el.findtext(f"yh:display_position", namespaces=NS),
                "team": el.findtext(f"yh:editorial_team_abbr", namespaces=NS),
            })

        start += 25
        if start % 100 == 0:
            print(f"  Fetched {start} Yahoo players...")
        time.sleep(0.1)

    print(f"Fetched {len(all_yahoo)} total Yahoo players")

    matched = 0
    unmatched = 0

    with get_session() as session:
        for yp in all_yahoo:
            if not yp["name"]:
                continue

            try:
                nhl_id = resolve_player(session, name=yp["name"])
            except Exception:
                nhl_id = None

            if not nhl_id:
                unmatched += 1
                continue

            player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
            if player:
                player.yahoo_player_id = yp["yahoo_id"]
                player.yahoo_positions = yp["positions"]
                matched += 1

    print(f"Matched {matched}, unmatched {unmatched}")
    return {"matched": matched, "unmatched": unmatched, "total": len(all_yahoo)}


def sync_all_rosters(league_key: str, session=None) -> dict:
    """Sync current rosters for all teams into the TeamRoster table.

    Fetches every team's roster from Yahoo and replaces the existing
    TeamRoster rows for this league. Each player is resolved to an NHL ID.

    Returns dict with counts: teams, players, unresolved.
    """
    teams = get_all_rosters(league_key)

    own_session = session is None
    if own_session:
        session = get_session().__enter__()

    try:
        session.query(TeamRoster).filter(TeamRoster.league_key == league_key).delete()

        total_players = 0
        unresolved = 0

        for team in teams:
            team_key = team.get("team_key")
            if not team_key:
                continue

            for player in team.get("roster", []):
                player_name = player.get("name")
                if not player_name:
                    continue

                try:
                    nhl_id = resolve_player(
                        session,
                        name=player_name,
                        team_abbrev=player.get("team"),
                        position=player.get("position"),
                    )
                except Exception:
                    nhl_id = None

                if not nhl_id:
                    unresolved += 1
                    continue

                session.add(TeamRoster(
                    league_key=league_key,
                    team_key=team_key,
                    nhl_id=nhl_id,
                ))
                total_players += 1

        if own_session:
            session.commit()

        return {"teams": len(teams), "players": total_players, "unresolved": unresolved}

    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "rosters":
        if len(sys.argv) < 3:
            print("Usage: python -m src.ingest.yahoo.sync rosters <league_key>")
            sys.exit(1)
        league_key = sys.argv[2]
        print(f"Syncing rosters for {league_key}...")
        result = sync_all_rosters(league_key)
        print(f"Synced {result['players']} players across {result['teams']} teams ({result['unresolved']} unresolved)")

    elif len(sys.argv) > 1 and sys.argv[1] == "league":
        from src.ingest.yahoo.league_sync import sync_league

        if len(sys.argv) < 3:
            print("Usage: python -m src.ingest.yahoo.sync league <league_key>")
            sys.exit(1)
        league_key = sys.argv[2]
        print(f"Syncing league {league_key}...")
        result = sync_league(league_key)
        print(f"Synced {result['draft_picks']} draft picks")
        print(f"Synced {result['transactions']} transaction records")
    else:
        from src.ingest.yahoo.client import get_user_leagues
        leagues = get_user_leagues()
        if leagues:
            key = leagues[0]["league_key"]
            print(f"Syncing players from {leagues[0]['name']} ({key})")
            sync_yahoo_players(key)
        else:
            print("No leagues found")

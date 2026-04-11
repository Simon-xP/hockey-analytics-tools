"""Sync Yahoo player data to our DB.

Fetches all players from Yahoo Fantasy, matches them to our Player records
by name, and stores the Yahoo player ID and positional eligibility.

Run once to build the mapping, then periodically to catch new players.
"""

import time
import xml.etree.ElementTree as ET

import requests

from src.core.db import get_session
from src.core.models import Player
from src.core.resolver import resolve_player
from src.ingest.yahoo.auth import get_access_token

NS = {"yh": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}


def sync_yahoo_players(league_key: str) -> dict:
    """Fetch all Yahoo players and map to our DB.

    Returns dict with counts: matched, unmatched, total.
    """
    token = get_access_token()
    if not token:
        raise ValueError("Not authenticated with Yahoo")

    # Paginate through all Yahoo players
    all_yahoo = []
    start = 0
    while True:
        url = (
            f"https://fantasysports.yahooapis.com/fantasy/v2"
            f"/league/{league_key}/players"
            f";status=ALL;start={start};count=25"
        )
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
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


if __name__ == "__main__":
    # Default to first league
    from src.ingest.yahoo.client import get_user_leagues
    leagues = get_user_leagues()
    if leagues:
        key = leagues[0]["league_key"]
        print(f"Syncing players from {leagues[0]['name']} ({key})")
        sync_yahoo_players(key)
    else:
        print("No leagues found")

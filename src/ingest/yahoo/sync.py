"""Sync Yahoo Fantasy data to our DB.

- Player ID mapping (yahoo_player_id, yahoo_positions)
- Draft picks (for roster reconstruction)
- Transactions (for roster/FA reconstruction at any point)

Run sync_league() to pull draft + transactions for backtesting.
"""

import time
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET

import httpx
from sqlalchemy.orm import Session

from src.core.db import get_session
from src.core.models import Player, YahooDraftPick, YahooTransaction
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


def _get_team_names(league_key: str, headers: dict) -> dict[str, str]:
    """Fetch team key -> team name mapping."""
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/teams"
    resp = httpx.get(url, headers=headers)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)

    team_names = {}
    for team in root.findall(".//yh:team", NS):
        key = team.find("yh:team_key", NS).text
        name = team.find("yh:name", NS).text
        team_names[key] = name
    return team_names


def sync_draft(league_key: str, session: Session | None = None) -> int:
    """Fetch and store all draft picks for a league.

    Returns number of picks synced.
    """
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Get team names
    team_names = _get_team_names(league_key, headers)

    # Get draft results with player info
    url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/draftresults;out=players"
    resp = httpx.get(url, headers=headers)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)

    picks = []
    for dr in root.findall(".//yh:draft_result", NS):
        pick = dr.find("yh:pick", NS).text
        rd = dr.find("yh:round", NS).text
        team_key = dr.find("yh:team_key", NS).text

        player = dr.find(".//yh:player", NS)
        if player is None:
            continue

        name = player.find("yh:name/yh:full", NS).text
        yahoo_id = player.find("yh:player_id", NS).text
        team_abbr = player.find("yh:editorial_team_abbr", NS)
        team_abbr = team_abbr.text if team_abbr is not None else None
        pos = player.find("yh:display_position", NS)
        pos = pos.text if pos is not None else None

        positions = [p.text for p in player.findall(".//yh:eligible_positions/yh:position", NS)]
        positions = [p for p in positions if p not in ("Util", "IR", "IR+", "NA")]

        picks.append({
            "league_key": league_key,
            "pick_number": int(pick),
            "round_number": int(rd),
            "fantasy_team_key": team_key,
            "fantasy_team_name": team_names.get(team_key, team_key),
            "yahoo_player_id": int(yahoo_id),
            "player_name": name,
            "nhl_team_abbrev": team_abbr,
            "position": pos,
            "eligible_positions": ",".join(positions) if positions else None,
        })

    # Store to database
    own_session = session is None
    if own_session:
        session = get_session().__enter__()

    try:
        # Clear existing draft picks for this league
        session.query(YahooDraftPick).filter(
            YahooDraftPick.league_key == league_key
        ).delete()

        # Insert new picks and resolve NHL IDs
        for p in picks:
            nhl_id = resolve_player(session, name=p["player_name"], team_abbrev=p["nhl_team_abbrev"])
            pick = YahooDraftPick(
                nhl_id=nhl_id,
                **p
            )
            session.add(pick)

        if own_session:
            session.commit()

        return len(picks)

    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.__exit__(None, None, None)


def sync_transactions(league_key: str, session: Session | None = None) -> int:
    """Fetch and store all transactions for a league.

    Returns number of transaction records synced.
    """
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    all_transactions = []
    start = 0
    count = 100

    while True:
        url = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/transactions;start={start};count={count}"
        resp = httpx.get(url, headers=headers)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)

        transactions = root.findall(".//yh:transaction", NS)
        if not transactions:
            break

        for t in transactions:
            tx_id = t.find("yh:transaction_id", NS).text
            tx_type = t.find("yh:type", NS).text
            timestamp = int(t.find("yh:timestamp", NS).text)
            tx_datetime = datetime.fromtimestamp(timestamp)

            for p in t.findall(".//yh:player", NS):
                name = p.find("yh:name/yh:full", NS).text
                yahoo_id_el = p.find("yh:player_id", NS)
                if yahoo_id_el is None:
                    continue
                yahoo_id = yahoo_id_el.text

                team_abbr = p.find("yh:editorial_team_abbr", NS)
                team_abbr = team_abbr.text if team_abbr is not None else None
                pos = p.find("yh:display_position", NS)
                pos = pos.text if pos is not None else None

                tx_data = p.find("yh:transaction_data", NS)
                if tx_data is None:
                    continue
                action = tx_data.find("yh:type", NS).text  # "add" or "drop"

                # Get fantasy team involved
                dest_team = tx_data.find("yh:destination_team_name", NS)
                src_team = tx_data.find("yh:source_team_name", NS)
                dest_key = tx_data.find("yh:destination_team_key", NS)
                src_key = tx_data.find("yh:source_team_key", NS)

                fantasy_team_name = dest_team.text if dest_team is not None else (
                    src_team.text if src_team is not None else None
                )
                fantasy_team_key = dest_key.text if dest_key is not None else (
                    src_key.text if src_key is not None else None
                )

                all_transactions.append({
                    "league_key": league_key,
                    "transaction_id": int(tx_id),
                    "transaction_type": tx_type,
                    "timestamp": tx_datetime,
                    "yahoo_player_id": int(yahoo_id),
                    "player_name": name,
                    "nhl_team_abbrev": team_abbr,
                    "position": pos,
                    "action": action,
                    "fantasy_team_key": fantasy_team_key,
                    "fantasy_team_name": fantasy_team_name,
                })

        start += count
        if len(transactions) < count:
            break

    # Store to database
    own_session = session is None
    if own_session:
        session = get_session().__enter__()

    try:
        # Clear existing transactions for this league
        session.query(YahooTransaction).filter(
            YahooTransaction.league_key == league_key
        ).delete()

        # Insert new transactions and resolve NHL IDs
        for t in all_transactions:
            nhl_id = resolve_player(session, name=t["player_name"], team_abbrev=t["nhl_team_abbrev"])
            tx = YahooTransaction(
                nhl_id=nhl_id,
                **t
            )
            session.add(tx)

        if own_session:
            session.commit()

        return len(all_transactions)

    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.__exit__(None, None, None)


def sync_league(league_key: str) -> dict:
    """Sync all Yahoo Fantasy data for a league (draft + transactions).

    Returns counts of synced records.
    """
    with get_session() as session:
        draft_count = sync_draft(league_key, session)
        tx_count = sync_transactions(league_key, session)
        session.commit()

    return {
        "draft_picks": draft_count,
        "transactions": tx_count,
    }


def get_my_roster_at(
    league_key: str,
    my_team_name: str,
    as_of: datetime,
    session: Session,
) -> list[dict]:
    """Reconstruct my roster at a specific point in time.

    Returns list of players with yahoo_player_id, player_name, nhl_id, position.
    """
    # Start with my draft picks
    draft_picks = (
        session.query(YahooDraftPick)
        .filter(
            YahooDraftPick.league_key == league_key,
            YahooDraftPick.fantasy_team_name == my_team_name,
        )
        .all()
    )

    roster = {
        p.yahoo_player_id: {
            "yahoo_player_id": p.yahoo_player_id,
            "player_name": p.player_name,
            "nhl_id": p.nhl_id,
            "position": p.position,
            "nhl_team": p.nhl_team_abbrev,
        }
        for p in draft_picks
    }

    # Apply my transactions up to as_of
    transactions = (
        session.query(YahooTransaction)
        .filter(
            YahooTransaction.league_key == league_key,
            YahooTransaction.fantasy_team_name == my_team_name,
            YahooTransaction.timestamp <= as_of,
        )
        .order_by(YahooTransaction.timestamp)
        .all()
    )

    for tx in transactions:
        if tx.action == "add":
            roster[tx.yahoo_player_id] = {
                "yahoo_player_id": tx.yahoo_player_id,
                "player_name": tx.player_name,
                "nhl_id": tx.nhl_id,
                "position": tx.position,
                "nhl_team": tx.nhl_team_abbrev,
            }
        elif tx.action == "drop":
            roster.pop(tx.yahoo_player_id, None)

    return list(roster.values())


def get_free_agents_at(
    league_key: str,
    as_of: datetime,
    session: Session,
    waiver_days: int = 4,
) -> list[dict]:
    """Get all players available as free agents at a specific point in time.

    A player is a free agent if:
    1. They were NOT drafted by anyone and were added to the pool (via a
       transaction) at least `waiver_days` ago, OR
    2. They were dropped at least `waiver_days` ago AND not picked up since

    The waiver period means a dropped player isn't immediately available.
    If someone picks them up during the waiver period, they were claimed
    on waivers and never became a true FA.

    Args:
        league_key: Yahoo league key
        as_of: Point in time to reconstruct FA pool
        session: DB session
        waiver_days: Days a player is on waivers after being dropped (default 4)

    Returns list of players with yahoo_player_id, player_name, nhl_id, position.
    """
    waiver_period = timedelta(days=waiver_days)

    # Get all drafted players (these start on rosters)
    drafted = (
        session.query(YahooDraftPick)
        .filter(YahooDraftPick.league_key == league_key)
        .all()
    )
    drafted_ids = {p.yahoo_player_id for p in drafted}

    # Get ALL transactions (not just up to as_of) so we can check waiver claims
    all_transactions = (
        session.query(YahooTransaction)
        .filter(YahooTransaction.league_key == league_key)
        .order_by(YahooTransaction.timestamp)
        .all()
    )

    # Track player state:
    # - "rostered": currently on someone's roster
    # - "waivers": dropped but waiver period hasn't cleared
    # - "fa": cleared waivers, available as free agent
    # Also track when they were dropped (for waiver calculation)
    player_state: dict[int, str] = {pid: "rostered" for pid in drafted_ids}
    drop_time: dict[int, datetime] = {}

    # All players we've seen (for returning player info)
    all_players = {
        p.yahoo_player_id: {
            "yahoo_player_id": p.yahoo_player_id,
            "player_name": p.player_name,
            "nhl_id": p.nhl_id,
            "position": p.position,
            "nhl_team": p.nhl_team_abbrev,
        }
        for p in drafted
    }

    for tx in all_transactions:
        pid = tx.yahoo_player_id

        # Track player info
        if pid not in all_players:
            all_players[pid] = {
                "yahoo_player_id": pid,
                "player_name": tx.player_name,
                "nhl_id": tx.nhl_id,
                "position": tx.position,
                "nhl_team": tx.nhl_team_abbrev,
            }

        # Only process transactions up to as_of for state changes
        if tx.timestamp > as_of:
            continue

        if tx.action == "add":
            # Player is now rostered (either FA pickup or waiver claim)
            player_state[pid] = "rostered"
            drop_time.pop(pid, None)  # clear any drop time

        elif tx.action == "drop":
            # Player goes on waivers
            player_state[pid] = "waivers"
            drop_time[pid] = tx.timestamp

    # Now determine who is actually a FA at as_of
    # Players on waivers become FA if their waiver period has cleared
    free_agents = []
    for pid, state in player_state.items():
        if state == "rostered":
            continue  # on a roster

        if state == "waivers":
            # Check if waiver period has cleared by as_of
            dropped_at = drop_time.get(pid)
            if dropped_at and (as_of >= dropped_at + waiver_period):
                # Waiver cleared, but check if someone claimed them during waiver
                # Look for any add between drop and waiver clear
                waiver_clear_time = dropped_at + waiver_period
                was_claimed = any(
                    tx.yahoo_player_id == pid
                    and tx.action == "add"
                    and dropped_at < tx.timestamp <= waiver_clear_time
                    for tx in all_transactions
                )
                if not was_claimed:
                    free_agents.append(all_players[pid])
            # else: still on waivers, not available

        elif state == "fa":
            # Already marked as FA
            free_agents.append(all_players[pid])

    return free_agents


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "league":
        # Sync draft + transactions
        if len(sys.argv) < 3:
            print("Usage: python -m src.ingest.yahoo.sync league <league_key>")
            sys.exit(1)
        league_key = sys.argv[2]
        print(f"Syncing league {league_key}...")
        result = sync_league(league_key)
        print(f"Synced {result['draft_picks']} draft picks")
        print(f"Synced {result['transactions']} transaction records")
    else:
        # Default: sync player IDs
        from src.ingest.yahoo.client import get_user_leagues
        leagues = get_user_leagues()
        if leagues:
            key = leagues[0]["league_key"]
            print(f"Syncing players from {leagues[0]['name']} ({key})")
            sync_yahoo_players(key)
        else:
            print("No leagues found")

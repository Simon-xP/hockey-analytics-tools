"""Sync Yahoo Fantasy league history: draft picks and transactions.

Pure ingestion — pulls from the Yahoo API into `yahoo_draft_picks` and
`yahoo_transactions`. The backtest layer reads those tables to reconstruct
rosters and FA pools for any past date, but nothing here knows about
backtesting.

Run `sync_league(league_key)` to pull a league's full history.
"""

import time
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from src.core.db import get_session
from src.core.models import YahooDraftPick, YahooTransaction
from src.core.resolver import resolve_player
from src.ingest.yahoo.auth import get_access_token

NS = {"yh": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}


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

    team_names = _get_team_names(league_key, headers)

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

    own_session = session is None
    if own_session:
        session = get_session().__enter__()

    try:
        session.query(YahooDraftPick).filter(
            YahooDraftPick.league_key == league_key
        ).delete()

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

    own_session = session is None
    if own_session:
        session = get_session().__enter__()

    try:
        session.query(YahooTransaction).filter(
            YahooTransaction.league_key == league_key
        ).delete()

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

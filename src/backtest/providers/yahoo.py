"""Yahoo Fantasy provider -- temporal-gated access to Yahoo league data.

Includes roster and FA pool reconstruction from synced draft + transaction
data, plus the YahooProvider interface used by the backtest engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from src.core.models import YahooDraftPick, YahooTransaction


def get_my_roster_at(
    league_key: str,
    my_team_name: str,
    as_of: datetime,
    session: Session,
) -> list[dict]:
    """Reconstruct my roster at a specific point in time.

    Processes ALL league transactions (not just ours) to correctly
    handle outgoing trades -- Yahoo records trades on the receiving
    team, so filtering to only our transactions misses departures.

    Returns list of players with yahoo_player_id, player_name, nhl_id, position.
    """
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

    transactions = (
        session.query(YahooTransaction)
        .filter(
            YahooTransaction.league_key == league_key,
            YahooTransaction.timestamp <= as_of,
        )
        .order_by(YahooTransaction.timestamp)
        .all()
    )

    for tx in transactions:
        pid = tx.yahoo_player_id
        if tx.fantasy_team_name == my_team_name:
            if tx.action in ("add", "trade"):
                roster[pid] = {
                    "yahoo_player_id": pid,
                    "player_name": tx.player_name,
                    "nhl_id": tx.nhl_id,
                    "position": tx.position,
                    "nhl_team": tx.nhl_team_abbrev,
                }
            elif tx.action == "drop":
                roster.pop(pid, None)
        else:
            if tx.action in ("add", "trade") and pid in roster:
                roster.pop(pid)

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

    drafted = (
        session.query(YahooDraftPick)
        .filter(YahooDraftPick.league_key == league_key)
        .all()
    )
    drafted_ids = {p.yahoo_player_id for p in drafted}

    all_transactions = (
        session.query(YahooTransaction)
        .filter(YahooTransaction.league_key == league_key)
        .order_by(YahooTransaction.timestamp)
        .all()
    )

    player_state: dict[int, str] = {pid: "rostered" for pid in drafted_ids}
    drop_time: dict[int, datetime] = {}

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

        if pid not in all_players:
            all_players[pid] = {
                "yahoo_player_id": pid,
                "player_name": tx.player_name,
                "nhl_id": tx.nhl_id,
                "position": tx.position,
                "nhl_team": tx.nhl_team_abbrev,
            }

        if tx.timestamp > as_of:
            continue

        if tx.action == "add":
            player_state[pid] = "rostered"
            drop_time.pop(pid, None)

        elif tx.action == "drop":
            player_state[pid] = "waivers"
            drop_time[pid] = tx.timestamp

    free_agents = []
    for pid, state in player_state.items():
        if state == "rostered":
            continue

        if state == "waivers":
            dropped_at = drop_time.get(pid)
            if dropped_at and (as_of >= dropped_at + waiver_period):
                waiver_clear_time = dropped_at + waiver_period
                was_claimed = any(
                    tx.yahoo_player_id == pid
                    and tx.action == "add"
                    and dropped_at < tx.timestamp <= waiver_clear_time
                    for tx in all_transactions
                )
                if not was_claimed:
                    free_agents.append(all_players[pid])

        elif state == "fa":
            free_agents.append(all_players[pid])

    return free_agents


@dataclass
class YahooProvider:
    session: Session
    as_of: date
    league_key: str
    team_name: str

    def _as_of_datetime(self) -> datetime:
        return datetime.combine(self.as_of, datetime.min.time())

    def get_roster(self) -> list[dict]:
        """Reconstruct roster as-of the decision date."""
        return get_my_roster_at(
            self.league_key,
            self.team_name,
            self._as_of_datetime(),
            self.session,
        )

    def get_free_agents(self) -> list[dict]:
        """Get FA pool as-of the decision date."""
        return get_free_agents_at(
            self.league_key,
            self._as_of_datetime(),
            self.session,
        )

    def get_transactions_in_range(
        self,
        start: date,
        end: date,
    ) -> tuple[list[dict], list[dict]]:
        """Get user's actual adds and drops in a date range.

        Returns (adds, drops) where each is a list of player dicts.
        """
        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end, datetime.max.time())

        transactions = (
            self.session.query(YahooTransaction)
            .filter(
                YahooTransaction.league_key == self.league_key,
                YahooTransaction.fantasy_team_name == self.team_name,
                YahooTransaction.timestamp >= start_dt,
                YahooTransaction.timestamp <= end_dt,
            )
            .order_by(YahooTransaction.timestamp)
            .all()
        )

        adds = []
        drops = []
        for tx in transactions:
            info = {
                "yahoo_player_id": tx.yahoo_player_id,
                "player_name": tx.player_name,
                "nhl_id": tx.nhl_id,
                "position": tx.position,
            }
            if tx.action == "add":
                adds.append(info)
            elif tx.action == "drop":
                drops.append(info)

        return adds, drops

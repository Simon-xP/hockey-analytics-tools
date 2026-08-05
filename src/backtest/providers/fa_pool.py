"""FA pool reconstruction — league-wide roster membership at any date.

Loads draft picks and all Yahoo transactions once, then answers
"which nhl_ids are unavailable as FAs on day D?" with per-day caching.

Models three player states:
- Rostered: on a team's roster (drafted, added, or traded)
- On waivers: dropped within the last N days, not yet claimable as FA
- Free agent: not rostered and waiver period has cleared
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from src.core.models import YahooDraftPick, YahooTransaction


class FAPoolReconstructor:
    """Determines which players are unavailable as FAs on a given day.

    Tracks roster membership AND waiver status across all teams.
    Constructed once per backtest run and reused across weeks/days.
    """

    def __init__(
        self,
        session: Session,
        league_key: str,
        waiver_days: int = 2,
    ):
        self._session = session
        self._league_key = league_key
        self._waiver_days = waiver_days
        self._drafts: list | None = None
        self._transactions: list | None = None
        self._cache: dict[date, tuple[set[int], set[int]]] = {}

    def _ensure_loaded(self) -> None:
        if self._drafts is not None:
            return

        self._drafts = (
            self._session.query(YahooDraftPick)
            .filter(YahooDraftPick.league_key == self._league_key)
            .all()
        )

        self._transactions = (
            self._session.query(YahooTransaction)
            .filter(YahooTransaction.league_key == self._league_key)
            .order_by(YahooTransaction.timestamp)
            .all()
        )

    def _compute_state(self, as_of: date) -> tuple[set[int], set[int]]:
        """Compute rostered and on-waivers sets for a date.

        Returns (rostered_nhl_ids, on_waivers_nhl_ids).
        """
        if as_of in self._cache:
            return self._cache[as_of]

        self._ensure_loaded()

        rostered: dict[int, int | None] = {}
        drop_log: dict[int, tuple[int | None, datetime]] = {}

        for pick in self._drafts:
            rostered[pick.yahoo_player_id] = pick.nhl_id

        cutoff = datetime.combine(as_of, datetime.max.time())
        for tx in self._transactions:
            if tx.timestamp > cutoff:
                break
            if tx.action in ("add", "trade"):
                rostered[tx.yahoo_player_id] = tx.nhl_id
                drop_log.pop(tx.yahoo_player_id, None)
            elif tx.action == "drop":
                rostered.pop(tx.yahoo_player_id, None)
                drop_log[tx.yahoo_player_id] = (tx.nhl_id, tx.timestamp)

        rostered_ids = {nid for nid in rostered.values() if nid is not None}

        waiver_cutoff = datetime.combine(
            as_of - timedelta(days=self._waiver_days),
            datetime.min.time(),
        )
        on_waivers = set()
        for _yahoo_pid, (nhl_id, drop_ts) in drop_log.items():
            if nhl_id and drop_ts >= waiver_cutoff:
                on_waivers.add(nhl_id)

        self._cache[as_of] = (rostered_ids, on_waivers)
        return rostered_ids, on_waivers

    def get_rostered_nhl_ids(self, as_of: date) -> set[int]:
        """Return nhl_ids on any team's roster as of end-of-day on as_of."""
        rostered, _ = self._compute_state(as_of)
        return rostered

    def get_unavailable_nhl_ids(self, as_of: date) -> set[int]:
        """Return nhl_ids that cannot be picked up as FA on as_of.

        Includes both rostered players and players on waivers
        (dropped within the last waiver_days days).
        """
        rostered, on_waivers = self._compute_state(as_of)
        return rostered | on_waivers

    @property
    def stats(self) -> dict:
        """Return loading stats for diagnostics."""
        self._ensure_loaded()
        return {
            "draft_picks": len(self._drafts),
            "transactions": len(self._transactions),
            "cached_days": len(self._cache),
            "waiver_days": self._waiver_days,
        }

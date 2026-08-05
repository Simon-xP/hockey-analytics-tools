"""Sync player valuations -- materialize ML forecasts into the database.

Run after each game day to recompute forecasts for all relevant players.
The transaction system reads from this table instead of computing on demand.

Usage:
    # Nightly sync: roster + top FAs + trending players
    python -m scripts.sync_valuations

    # Sync a specific player
    python -m scripts.sync_valuations --player 8478402

    # Sync your roster only
    python -m scripts.sync_valuations --roster-only

    # Sync free agents only
    python -m scripts.sync_valuations --fa-only

    # Sync trending players only
    python -m scripts.sync_valuations --trending-only

    # Sync with a custom lookahead window
    python -m scripts.sync_valuations --weeks-ahead 4

    # Sync as of a specific date (for backtesting)
    python -m scripts.sync_valuations --as-of 2026-03-01
"""

import argparse
import logging
import os
from datetime import date, datetime

from src.core.db import get_session
from src.optimize.sync import (
    sync_free_agents,
    sync_nightly,
    sync_player,
    sync_roster_players,
    sync_transaction_trends,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Sync player valuations")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--player", type=int, nargs="+",
        help="Specific NHL player ID(s) to sync",
    )
    group.add_argument(
        "--roster-only", action="store_true",
        help="Only sync your Yahoo roster",
    )
    group.add_argument(
        "--fa-only", action="store_true",
        help="Only sync top free agents",
    )
    group.add_argument(
        "--trending-only", action="store_true",
        help="Only sync trending players",
    )
    parser.add_argument(
        "--weeks-ahead", type=int, default=3,
        help="How many weeks of games to forecast (default: 3)",
    )
    parser.add_argument(
        "--as-of", type=str, default=None,
        help="Knowledge cutoff date (YYYY-MM-DD), defaults to today",
    )
    parser.add_argument(
        "--top-fa", type=int, default=50,
        help="Number of top free agents to include (default: 50)",
    )
    parser.add_argument(
        "--season", type=str, default="20252026",
    )
    args = parser.parse_args()

    from_date = (
        datetime.strptime(args.as_of, "%Y-%m-%d").date()
        if args.as_of else date.today()
    )

    league_key = os.environ.get("YAHOO_LEAGUE_KEY", "")

    with get_session() as session:
        if args.player:
            for nhl_id in args.player:
                log.info("Syncing player %d", nhl_id)
                sync_player(
                    session, nhl_id,
                    from_date=from_date,
                    season=args.season,
                    weeks_ahead=args.weeks_ahead,
                )
            session.commit()
        elif args.roster_only:
            sync_roster_players(
                session, league_key,
                from_date=from_date,
                season=args.season,
                weeks_ahead=args.weeks_ahead,
            )
        elif args.fa_only:
            sync_free_agents(
                session, league_key,
                count=args.top_fa,
                from_date=from_date,
                season=args.season,
                weeks_ahead=args.weeks_ahead,
            )
        elif args.trending_only:
            sync_transaction_trends(
                session, league_key,
                from_date=from_date,
                season=args.season,
                weeks_ahead=args.weeks_ahead,
            )
        else:
            counts = sync_nightly(
                session, league_key,
                from_date=from_date,
                season=args.season,
                weeks_ahead=args.weeks_ahead,
                fa_count=args.top_fa,
            )
            log.info(
                "Done: %d synced (%d roster, %d FA, %d trending)",
                counts["synced"], counts["roster"],
                counts["free_agents"], counts["trending"],
            )


if __name__ == "__main__":
    main()

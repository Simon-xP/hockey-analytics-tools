#!/usr/bin/env python3
"""
NST Game Log Ingestion CLI.

Budget-aware, resumable scraper for per-game player stats from Natural Stat Trick.
Designed to be run daily (e.g., via cron) with a request budget to avoid rate limiting.

Usage:
    # Scrape current season with default budget (100 requests)
    python scripts/ingest_nst_game_logs.py --season 2025

    # Scrape with custom budget
    python scripts/ingest_nst_game_logs.py --season 2025 --budget 150

    # Single player (for testing)
    python scripts/ingest_nst_game_logs.py --season 2025 --player-id 8483570

    # Check progress
    python scripts/ingest_nst_game_logs.py --status

    # Check progress for specific season
    python scripts/ingest_nst_game_logs.py --status --season 2023
"""

import argparse
import sys

from src.ingest.natural_stat_trick.scraper import (
    CURRENT_SEASON,
    game_log_status,
    scrape_all_game_logs,
)


def main():
    parser = argparse.ArgumentParser(
        description="NST game log ingestion (budget-aware, resumable)"
    )
    parser.add_argument(
        "--season", type=int,
        help="Season start year (e.g., 2025 for 2025-26)"
    )
    parser.add_argument(
        "--budget", type=int, default=100,
        help="Max requests for this run (default: 100)"
    )
    parser.add_argument(
        "--player-id", type=int,
        help="Scrape a single player (for testing)"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show scraping progress and exit"
    )

    args = parser.parse_args()

    if args.status:
        game_log_status(args.season)
        return

    if not args.season:
        print(f"No --season specified, using current season ({CURRENT_SEASON})")
        args.season = CURRENT_SEASON

    result = scrape_all_game_logs(
        season_year=args.season,
        budget=args.budget,
        player_id=args.player_id,
    )

    if result.get("error") == "no_players":
        print("\nRun the season stats scraper first:")
        print("  python -m src.ingest.natural_stat_trick.scraper --current")
        sys.exit(1)

    if result.get("stopped") == "rate_limited":
        print("\nRate limited. Resume later with the same command.")
        sys.exit(2)


if __name__ == "__main__":
    main()

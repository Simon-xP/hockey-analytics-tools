"""Daily pipeline — run after each day's games complete.

Orchestrates the full data flow:
  1. Ingest play-by-play events and shifts for completed games
  2. Build feature-enriched shot attempts from new events
  3. Score new shots with the trained xG model

By default processes yesterday's games (NHL games typically finish by ~1am ET).
Can also process a specific date or date range.

Usage:
    # Process yesterday's games (typical nightly run)
    python -m scripts.daily_pipeline

    # Process a specific date
    python -m scripts.daily_pipeline --date 2026-04-08

    # Process a date range
    python -m scripts.daily_pipeline --from 2026-04-01 --to 2026-04-08

    # Skip xG scoring (e.g., model not trained yet)
    python -m scripts.daily_pipeline --no-score
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from src.core.db import init_db
from scripts.ingest_game_events import get_game_ids_for_date_range, ingest_games
from scripts.build_shot_attempts import process_games as build_shots
from scripts.build_shot_attempts import get_ingested_game_ids


def run_pipeline(
    target_date: date | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    score: bool = True,
) -> dict:
    """Run the full daily pipeline.

    Args:
        target_date: Single date to process (default: yesterday).
        from_date: Start of date range.
        to_date: End of date range.
        score: Whether to run xG scoring (requires trained model).

    Returns:
        Summary dict with counts from each step.
    """
    # Determine date range
    if from_date and to_date:
        start, end = from_date, to_date
    elif target_date:
        start = end = target_date
    else:
        start = end = date.today() - timedelta(days=1)

    print(f"{'='*60}")
    print(f"DAILY PIPELINE: {start} to {end}")
    print(f"{'='*60}")

    summary = {}

    # Step 1: Ingest events and shifts
    print(f"\n--- Step 1: Ingest events and shifts ---")
    game_ids = get_game_ids_for_date_range(start, end)

    if not game_ids:
        print("  No completed games found.")
        summary["games_found"] = 0
        return summary

    print(f"  Found {len(game_ids)} completed games")
    summary["games_found"] = len(game_ids)

    ingest_result = ingest_games(game_ids)
    summary["ingest"] = ingest_result
    print(f"  Ingested: {ingest_result['games_ingested']} games, "
          f"{ingest_result['events']} events, {ingest_result['shifts']} shifts")

    if ingest_result["errors"] > 0:
        print(f"  Errors: {ingest_result['errors']}")

    # Step 2: Build shot attempts
    print(f"\n--- Step 2: Build shot attempts ---")
    shots_result = build_shots(game_ids)
    summary["shots"] = shots_result
    print(f"  Processed: {shots_result['games']} games, "
          f"{shots_result['shots']} shots, {shots_result['goals']} goals")

    # Step 3: Score with xG model
    if score:
        print(f"\n--- Step 3: Score with xG model ---")
        model_path = Path("models/xg/xg_latest.pkl")
        if not model_path.exists():
            print("  No trained model found. Skipping scoring.")
            print(f"  Train one with: python -m scripts.train_xg_model")
            summary["scoring"] = {"skipped": True, "reason": "no model"}
        else:
            from scripts.score_xg import score_shots

            # Determine seasons from game IDs
            season_years = set()
            for gid in game_ids:
                year = gid // 1_000_000
                season_years.add(f"{year}{year+1}")
            seasons = sorted(season_years)

            score_result = score_shots(seasons=seasons, force=True)
            summary["scoring"] = score_result
            print(f"  Scored: {score_result['scored']} shots")
    else:
        print(f"\n--- Step 3: Skipped (--no-score) ---")
        summary["scoring"] = {"skipped": True, "reason": "flag"}

    print(f"\n{'='*60}")
    print("DONE")
    print(f"{'='*60}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Daily pipeline: ingest → shot features → xG scoring"
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="Date to process (YYYY-MM-DD). Default: yesterday.",
    )
    parser.add_argument(
        "--from", dest="from_date", type=str, default=None,
        help="Start date for range (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--to", dest="to_date", type=str, default=None,
        help="End date for range (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--no-score", action="store_true",
        help="Skip xG scoring step",
    )

    args = parser.parse_args()
    init_db()

    target = None
    from_d = None
    to_d = None

    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
    if args.from_date:
        from_d = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    if args.to_date:
        to_d = datetime.strptime(args.to_date, "%Y-%m-%d").date()

    run_pipeline(
        target_date=target,
        from_date=from_d,
        to_date=to_d,
        score=not args.no_score,
    )

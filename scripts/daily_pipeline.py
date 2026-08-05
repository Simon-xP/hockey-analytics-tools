"""Daily pipeline — run after each day's games complete.

Orchestrates the full data flow:
  1. Ingest play-by-play events and shifts for completed games
  2. Build feature-enriched shot attempts from new events
  3. Score new shots with the trained xG model
  4. Build the per-goalie game log from those shots and shifts
  5. Sync player valuations

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
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from scripts.build_shot_attempts import process_games as build_shots
from scripts.ingest_game_events import get_game_ids_for_date_range, ingest_games
from src.core.db import init_db


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
    print("\n--- Step 1: Ingest events and shifts ---")
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
    print("\n--- Step 2: Build shot attempts ---")
    shots_result = build_shots(game_ids)
    summary["shots"] = shots_result
    print(f"  Processed: {shots_result['games']} games, "
          f"{shots_result['shots']} shots, {shots_result['goals']} goals")

    # Step 3: Score with xG model
    if score:
        print("\n--- Step 3: Score with xG model ---")
        model_path = Path("models/xg/xg_latest.pkl")
        if not model_path.exists():
            print("  No trained model found. Skipping scoring.")
            print("  Train one with: python -m scripts.train_xg_model")
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
        print("\n--- Step 3: Skipped (--no-score) ---")
        summary["scoring"] = {"skipped": True, "reason": "flag"}

    # Step 4: Build goalie game log
    # Runs after xG scoring so the log picks up scored shots in the same
    # pass. Depends on shifts from step 1 to tell starts from relief.
    print("\n--- Step 4: Build goalie game log ---")
    from scripts.build_goalie_game_log import (  # noqa: I001
        get_candidate_games,
        process_games as build_goalie_log,
    )
    from src.core.db import get_session as _get_session

    with _get_session() as sess:
        candidates = [
            g for g in get_candidate_games(sess, seasons=None)
            if g["game_id"] in set(game_ids)
        ]

    if not candidates:
        print("  No completed games with shot data.")
        summary["goalie_log"] = {"games": 0}
    else:
        goalie_result = build_goalie_log(candidates, force=True)
        summary["goalie_log"] = goalie_result
        print(f"  Built: {goalie_result['games']} games, "
              f"{goalie_result['rows']} goalie lines "
              f"({goalie_result['starts']} starts)")
        if goalie_result["no_shifts"]:
            print(f"  Warning: {goalie_result['no_shifts']} games had no "
                  f"detectable start (missing shift data)")

    # Step 5: Sync player valuations
    print("\n--- Step 5: Sync player valuations ---")
    model_dir = Path("models/forecasting_v2")
    if not model_dir.exists():
        print("  No forecasting models found. Skipping valuation sync.")
        print("  Train with: python -m scripts.train_forecasting")
        summary["valuations"] = {"skipped": True, "reason": "no model"}
    else:
        from src.core.db import get_session
        from src.optimize.sync import sync_nightly

        league_key = os.environ.get("YAHOO_LEAGUE_KEY", "")
        if not league_key:
            print("  YAHOO_LEAGUE_KEY not set. Skipping valuation sync.")
            summary["valuations"] = {"skipped": True, "reason": "no league key"}
        else:
            with get_session() as sess:
                counts = sync_nightly(
                    sess, league_key,
                    from_date=end + timedelta(days=1),
                    season=_season_for_date(end),
                )

            summary["valuations"] = counts
            print(
                f"  Synced: {counts['synced']} valuations "
                f"({counts['roster']} roster, {counts['free_agents']} FA, "
                f"{counts['trending']} trending)"
            )

    print(f"\n{'='*60}")
    print("DONE")
    print(f"{'='*60}")

    return summary


def _season_for_date(d: date) -> str:
    start_year = d.year if d.month >= 8 else d.year - 1
    return f"{start_year}{start_year + 1}"


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

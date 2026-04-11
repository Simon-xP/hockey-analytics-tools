"""Ingest play-by-play events and shift data from the NHL API.

Fetches raw event and shift data for completed games and stores them in
the game_events and player_shifts tables. Safe to re-run — skips games
that have already been ingested.

Usage:
    # Ingest all completed games from yesterday
    python -m scripts.ingest_game_events --date 2026-04-08

    # Ingest a date range
    python -m scripts.ingest_game_events --from 2025-10-04 --to 2025-10-10

    # Ingest all completed games for a season (uses games table)
    python -m scripts.ingest_game_events --season 20252026

    # Ingest a single game by ID
    python -m scripts.ingest_game_events --game-id 2025021246
"""

import argparse
from datetime import date, datetime, timedelta

from sqlalchemy import func

from src.core.db import get_session, init_db
from src.core.models import Game, GameEvent, PlayerShift
from src.ingest.nhl_api.client import (
    get_game_play_by_play,
    get_game_shifts,
    get_completed_games,
)


def get_ingested_game_ids(session) -> set[int]:
    """Return set of game IDs that already have events ingested."""
    rows = session.query(GameEvent.game_id).distinct().all()
    return {r[0] for r in rows}


def ingest_game(game_id: int, session) -> dict:
    """
    Ingest play-by-play events and shifts for a single game.

    Returns dict with counts: events, shifts.
    """
    # Fetch from NHL API
    events = get_game_play_by_play(game_id)
    shifts = get_game_shifts(game_id)

    # Store events
    event_count = 0
    for e in events:
        event = GameEvent(
            game_id=game_id,
            event_id=e["event_id"],
            period=e["period"],
            period_type=e["period_type"],
            time_in_period=e["time_in_period"],
            time_remaining=e["time_remaining"],
            event_type=e["event_type"],
            situation_code=e["situation_code"],
            sort_order=e["sort_order"],
            x_coord=e["x_coord"],
            y_coord=e["y_coord"],
            zone_code=e["zone_code"],
            player_1_id=e["player_1_id"],
            player_2_id=e["player_2_id"],
            team_id=e["team_id"],
            shot_type=e["shot_type"],
            detail=e["detail"],
        )
        session.add(event)
        event_count += 1

    # Store shifts
    shift_count = 0
    for s in shifts:
        shift = PlayerShift(
            game_id=game_id,
            player_id=s["player_id"],
            shift_number=s["shift_number"],
            period=s["period"],
            start_time=s["start_time"],
            end_time=s["end_time"],
            duration=s["duration"],
            team_id=s["team_id"],
        )
        session.add(shift)
        shift_count += 1

    return {"events": event_count, "shifts": shift_count}


def ingest_games(game_ids: list[int], commit_every: int = 50) -> dict:
    """
    Ingest events and shifts for a list of game IDs, skipping already-ingested.

    Commits to DB every `commit_every` games so progress isn't lost on crash.

    Returns dict with total counts: games_ingested, games_skipped, events, shifts.
    """
    totals = {"games_ingested": 0, "games_skipped": 0, "events": 0, "shifts": 0, "errors": 0}
    batch_count = 0

    with get_session() as session:
        already_done = get_ingested_game_ids(session)

        for i, game_id in enumerate(game_ids):
            if game_id in already_done:
                totals["games_skipped"] += 1
                continue

            try:
                # Use savepoint so a single game failure doesn't kill the batch
                nested = session.begin_nested()
                counts = ingest_game(game_id, session)
                nested.commit()
                totals["events"] += counts["events"]
                totals["shifts"] += counts["shifts"]
                totals["games_ingested"] += 1
                batch_count += 1
            except Exception as e:
                nested.rollback()
                print(f"  Error ingesting game {game_id}: {e}")
                totals["errors"] += 1
                continue

            # Commit periodically so progress isn't lost
            if batch_count >= commit_every:
                session.commit()
                batch_count = 0

            done = totals["games_ingested"]
            if done % 25 == 0 or i == len(game_ids) - 1:
                print(
                    f"  Progress: {i + 1}/{len(game_ids)} checked, "
                    f"{done} ingested, {totals['games_skipped']} skipped, "
                    f"{totals['events']} events, {totals['shifts']} shifts",
                    flush=True,
                )

    return totals


def get_game_ids_for_season(season: str) -> list[int]:
    """Get all completed game IDs for a season from the games table."""
    with get_session() as session:
        rows = (
            session.query(Game.game_id)
            .filter(
                Game.game_id >= int(season[:4]) * 1_000_000,
                Game.game_id < (int(season[:4]) + 1) * 1_000_000,
                Game.home_score.isnot(None),
            )
            .order_by(Game.date)
            .all()
        )
    return [r[0] for r in rows]


def get_game_ids_for_date_range(start: date, end: date) -> list[int]:
    """Get completed game IDs for a date range from the NHL schedule API."""
    game_ids = []
    current = start
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        try:
            day_games = get_completed_games(date_str)
            game_ids.extend(day_games)
            if day_games:
                print(f"  {date_str}: {len(day_games)} games")
        except Exception as e:
            print(f"  {date_str}: error fetching schedule: {e}")
        current += timedelta(days=1)
    return game_ids


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest play-by-play events and shifts from NHL API"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--date", type=str,
        help="Single date to ingest (YYYY-MM-DD)",
    )
    group.add_argument(
        "--season", nargs="+",
        help="Season(s) to ingest, e.g. 20242025 20252026",
    )
    group.add_argument(
        "--game-id", type=int,
        help="Single game ID to ingest",
    )
    parser.add_argument(
        "--from", dest="from_date", type=str,
        help="Start date for range (YYYY-MM-DD), use with --to",
    )
    parser.add_argument(
        "--to", dest="to_date", type=str,
        help="End date for range (YYYY-MM-DD), use with --from",
    )

    args = parser.parse_args()
    init_db()

    if args.game_id:
        print(f"Ingesting single game: {args.game_id}")
        game_ids = [args.game_id]

    elif args.date:
        # Support --date with --from/--to for range
        if args.from_date and args.to_date:
            start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
            end = datetime.strptime(args.to_date, "%Y-%m-%d").date()
        else:
            start = datetime.strptime(args.date, "%Y-%m-%d").date()
            end = start
        print(f"Fetching schedule for {start} to {end}...")
        game_ids = get_game_ids_for_date_range(start, end)

    elif args.season:
        game_ids = []
        for season in args.season:
            print(f"\nLooking up games for season {season}...")
            season_games = get_game_ids_for_season(season)
            print(f"  Found {len(season_games)} completed games in DB")
            game_ids.extend(season_games)

    if not game_ids:
        print("No games to ingest.")
    else:
        print(f"\nIngesting {len(game_ids)} games...")
        totals = ingest_games(game_ids)
        print(f"\nDone: {totals['games_ingested']} games ingested, "
              f"{totals['games_skipped']} skipped, {totals['errors']} errors")
        print(f"  {totals['events']} events, {totals['shifts']} shifts stored")

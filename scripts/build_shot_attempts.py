"""Build shot_attempts table from ingested game_events.

Processes play-by-play events into feature-enriched shot attempts for
xG model training. Safe to re-run — skips games already processed.

Usage:
    # Process all ingested games
    python -m scripts.build_shot_attempts

    # Process a specific season
    python -m scripts.build_shot_attempts --season 20252026

    # Process a single game
    python -m scripts.build_shot_attempts --game-id 2025021246
"""

import argparse

from sqlalchemy import text

from src.core.db import get_session, init_db
from src.core.models import Game, ShotAttempt
from src.analytics.xg.features import build_shot_attempts_for_game


def get_ingested_game_ids(session) -> set[int]:
    """Game IDs that have events in game_events."""
    rows = session.execute(
        text("SELECT DISTINCT game_id FROM game_events")
    ).fetchall()
    return {r[0] for r in rows}


def get_processed_game_ids(session) -> set[int]:
    """Game IDs that already have shot_attempts."""
    rows = session.execute(
        text("SELECT DISTINCT game_id FROM shot_attempts")
    ).fetchall()
    return {r[0] for r in rows}


def process_games(game_ids: list[int]) -> dict:
    """Process a list of game IDs into shot_attempts."""
    totals = {"games": 0, "skipped": 0, "shots": 0, "goals": 0, "errors": 0}

    with get_session() as session:
        already_done = get_processed_game_ids(session)

        # Preload game info (home/away teams)
        game_info = {}
        games = (
            session.query(Game)
            .filter(Game.game_id.in_(game_ids))
            .all()
        )
        for g in games:
            game_info[g.game_id] = {
                "home_team_id": g.home_team_id,
                "away_team_id": g.away_team_id,
            }

        for i, game_id in enumerate(game_ids):
            if game_id in already_done:
                totals["skipped"] += 1
                continue

            info = game_info.get(game_id)
            if not info:
                totals["errors"] += 1
                continue

            try:
                shots = build_shot_attempts_for_game(
                    session,
                    game_id,
                    home_team_id=info["home_team_id"],
                    away_team_id=info["away_team_id"],
                )
                for s in shots:
                    session.add(s)
                session.flush()

                goals = sum(1 for s in shots if s.is_goal)
                totals["games"] += 1
                totals["shots"] += len(shots)
                totals["goals"] += goals
            except Exception as e:
                print(f"  Error processing game {game_id}: {e}")
                totals["errors"] += 1
                continue

            done = totals["games"]
            if done % 100 == 0 or i == len(game_ids) - 1:
                print(
                    f"  Progress: {i + 1}/{len(game_ids)} checked, "
                    f"{done} processed, {totals['shots']} shots, "
                    f"{totals['goals']} goals"
                )

    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build shot_attempts from game_events"
    )
    parser.add_argument(
        "--season", nargs="+",
        help="Season(s) to process, e.g. 20242025 20252026",
    )
    parser.add_argument(
        "--game-id", type=int,
        help="Single game ID to process",
    )
    args = parser.parse_args()
    init_db()

    with get_session() as session:
        ingested = get_ingested_game_ids(session)

    if args.game_id:
        game_ids = [args.game_id]
    elif args.season:
        game_ids = []
        for season in args.season:
            start = int(season[:4]) * 1_000_000
            end = (int(season[:4]) + 1) * 1_000_000
            season_games = sorted(gid for gid in ingested if start <= gid < end)
            print(f"Season {season}: {len(season_games)} ingested games")
            game_ids.extend(season_games)
    else:
        game_ids = sorted(ingested)
        print(f"All ingested games: {len(game_ids)}")

    if not game_ids:
        print("No games to process.")
    else:
        print(f"\nProcessing {len(game_ids)} games...")
        totals = process_games(game_ids)
        print(f"\nDone: {totals['games']} games processed, "
              f"{totals['skipped']} skipped, {totals['errors']} errors")
        print(f"  {totals['shots']} shot attempts, {totals['goals']} goals "
              f"({totals['goals']/max(totals['shots'],1)*100:.1f}% shooting)")

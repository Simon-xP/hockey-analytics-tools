"""Load game results from the NHL API club schedule endpoint.

Fetches completed regular-season game results for all teams and either
creates or updates Game rows with scores. Safe to re-run — skips games
that already have scores.

Usage:
    python -m scripts.backfill_game_scores                    # current season
    python -m scripts.backfill_game_scores --season 20242025  # specific season
    python -m scripts.backfill_game_scores --season 20232024 20242025 20252026
"""

import argparse
import time
from datetime import datetime

from src.core.db import get_session, init_db
from src.core.models import Game, Team
from src.ingest.nhl_api.client import get_team_schedule


def backfill_scores(season: str) -> dict:
    """
    Fetch game results from NHL API and upsert into the games table.

    For each team, fetches their season schedule. Each game appears on two
    teams' schedules, so we deduplicate by game_id. Creates new Game rows
    for games not already in the DB, and updates scores on existing rows
    that are missing them.

    Returns dict with counts: created, updated, skipped.
    """
    seen_game_ids = set()
    results_by_game_id = {}

    with get_session() as session:
        teams = session.query(Team).all()
        team_abbrevs = [t.abbrev for t in teams]
        team_id_by_abbrev = {t.abbrev: t.team_id for t in teams}

    print(f"Fetching {season} results for {len(team_abbrevs)} teams...")

    for i, abbrev in enumerate(team_abbrevs):
        time.sleep(0.5)
        try:
            games = get_team_schedule(abbrev, season)
        except Exception as e:
            print(f"  Warning: Failed to fetch {abbrev}: {e}")
            continue

        for g in games:
            gid = g["game_id"]
            if gid not in seen_game_ids:
                seen_game_ids.add(gid)
                results_by_game_id[gid] = g

        if (i + 1) % 8 == 0:
            print(f"  Fetched {i + 1}/{len(team_abbrevs)} teams "
                  f"({len(results_by_game_id)} unique games)")

    print(f"Found {len(results_by_game_id)} unique completed games")

    created = 0
    updated = 0
    skipped = 0

    with get_session() as session:
        for gid, result in results_by_game_id.items():
            game = session.query(Game).filter(Game.game_id == gid).first()

            if game:
                if game.home_score is not None:
                    skipped += 1
                    continue
                game.home_score = result["home_score"]
                game.away_score = result["away_score"]
                updated += 1
            else:
                home_id = team_id_by_abbrev.get(result["home_abbrev"])
                away_id = team_id_by_abbrev.get(result["away_abbrev"])
                if not home_id or not away_id:
                    continue

                game = Game(
                    game_id=gid,
                    date=datetime.strptime(result["game_date"], "%Y-%m-%d").date(),
                    home_team_id=home_id,
                    away_team_id=away_id,
                    home_score=result["home_score"],
                    away_score=result["away_score"],
                )
                session.add(game)
                created += 1

    print(f"Created {created}, updated {updated}, "
          f"skipped {skipped} (already had scores)")

    return {"created": created, "updated": updated, "skipped": skipped}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill game scores from NHL API")
    parser.add_argument(
        "--season", nargs="+", default=["20252026"],
        help="Season(s) to backfill, e.g. 20242025 20252026",
    )
    args = parser.parse_args()

    init_db()

    for season in args.season:
        print(f"\n{'='*60}")
        print(f"Season: {season}")
        print(f"{'='*60}")
        backfill_scores(season)

"""Re-fetch shifts for games that have events but missing shift data.

Some games were ingested with events but no shifts (the NHL API may have
returned empty or our ingestion missed them). This script finds those
games and re-fetches their shift data.
"""

import argparse

from sqlalchemy import text

from src.core.db import get_session, init_db
from src.core.models import PlayerShift
from src.ingest.nhl_api.client import get_game_shifts


def find_missing_shift_games(session, season_start_year: int | None = None) -> list[int]:
    """Find game IDs that have events but no shifts."""
    where = ""
    params = {}
    if season_start_year is not None:
        where = "WHERE game_id >= :s AND game_id < :e"
        params = {
            "s": season_start_year * 1_000_000,
            "e": (season_start_year + 1) * 1_000_000,
        }

    rows = session.execute(text(f"""
        WITH event_games AS (
            SELECT DISTINCT game_id FROM game_events {where}
        ),
        shift_games AS (
            SELECT DISTINCT game_id FROM player_shifts {where}
        )
        SELECT game_id FROM event_games
        WHERE game_id NOT IN (SELECT game_id FROM shift_games)
        ORDER BY game_id
    """), params).fetchall()

    return [r[0] for r in rows]


def refetch_shifts(game_ids: list[int]) -> dict:
    """Re-fetch shifts for a list of games."""
    totals = {"games": 0, "shifts": 0, "empty": 0, "errors": 0}

    with get_session() as session:
        batch_count = 0
        for i, game_id in enumerate(game_ids):
            try:
                shifts_data = get_game_shifts(game_id)
            except Exception as e:
                print(f"  Error fetching {game_id}: {e}")
                totals["errors"] += 1
                continue

            if not shifts_data:
                totals["empty"] += 1
                totals["games"] += 1
                continue

            for s in shifts_data:
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
                totals["shifts"] += 1

            totals["games"] += 1
            batch_count += 1

            if batch_count >= 50:
                session.commit()
                batch_count = 0

            if (i + 1) % 25 == 0:
                print(
                    f"  Progress: {i + 1}/{len(game_ids)}, "
                    f"shifts={totals['shifts']}, empty={totals['empty']}",
                    flush=True,
                )

    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Re-fetch shifts for games missing shift data"
    )
    parser.add_argument("--season", type=str, default=None,
                        help="Season to check (e.g., 20252026)")
    args = parser.parse_args()
    init_db()

    season_year = int(args.season[:4]) if args.season else None

    with get_session() as session:
        missing = find_missing_shift_games(session, season_year)

    print(f"Found {len(missing)} games missing shifts")
    if not missing:
        print("Nothing to do.")
    else:
        totals = refetch_shifts(missing)
        print(f"\nDone: {totals['games']} games processed, "
              f"{totals['shifts']} shifts added, "
              f"{totals['empty']} games legitimately have no shift data, "
              f"{totals['errors']} errors")

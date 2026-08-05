"""Add missing goalies to the players table.

Historical goalies show up in `shot_attempts.goalie_id` years before they
show up on a current NHL roster, and many never will again. The `players`
table is seeded from current rosters only, so any career-level goalie
feature silently drops those players.

This walks every distinct `goalie_id` in the shot data, finds the ones with
no `players` row, and fills them in from the NHL player landing endpoint.

Usage:
    python -m scripts.backfill_goalie_identities
    python -m scripts.backfill_goalie_identities --dry-run
"""

import argparse
from datetime import datetime

from sqlalchemy import text

from src.core.db import get_session, init_db
from src.core.models import Player, Team
from src.core.resolver.normalize import normalize_name
from src.ingest.nhl_api.client import get_player_landing


def find_missing_goalie_ids(session) -> list[int]:
    """Goalie IDs present in shot data but absent from `players`."""
    rows = session.execute(
        text("""
            SELECT DISTINCT sa.goalie_id
            FROM shot_attempts sa
            LEFT JOIN players p ON p.nhl_id = sa.goalie_id
            WHERE sa.goalie_id IS NOT NULL AND p.nhl_id IS NULL
            ORDER BY sa.goalie_id
        """)
    ).scalars().all()
    return list(rows)


def backfill(player_ids: list[int], dry_run: bool = False) -> dict:
    totals = {"added": 0, "not_found": 0, "errors": 0, "non_goalie": 0}

    with get_session() as session:
        teams = {t.abbrev: t.team_id for t in session.query(Team).all()}

        for i, player_id in enumerate(player_ids):
            try:
                info = get_player_landing(player_id)
            except Exception as e:  # noqa: BLE001 - keep going past one bad ID
                print(f"  Error fetching {player_id}: {e}")
                totals["errors"] += 1
                continue

            if not info:
                print(f"  No profile for {player_id}")
                totals["not_found"] += 1
                continue

            # The shot data says these faced shots as a goalie, so anything
            # coming back as a skater means the ID is suspect. Record it
            # rather than writing a wrong position.
            if info["position"] != "G":
                print(f"  {player_id} {info['full_name']} is listed as "
                      f"{info['position']}, not G. Skipping.")
                totals["non_goalie"] += 1
                continue

            birth_date = None
            if info.get("birth_date"):
                try:
                    birth_date = datetime.strptime(
                        info["birth_date"], "%Y-%m-%d"
                    ).date()
                except ValueError:
                    pass

            if dry_run:
                print(f"  Would add {player_id}: {info['full_name']} "
                      f"({info.get('team_abbrev')})")
                totals["added"] += 1
                continue

            session.add(Player(
                nhl_id=info["nhl_id"],
                full_name=info["full_name"],
                normalized_name=normalize_name(info["full_name"]),
                team_id=teams.get(info.get("team_abbrev")),
                position="G",
                birth_date=birth_date,
            ))
            totals["added"] += 1

            if totals["added"] % 25 == 0:
                session.commit()
                print(f"  Progress: {i + 1}/{len(player_ids)}, "
                      f"{totals['added']} added", flush=True)

    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill goalies missing from the players table"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    init_db()

    with get_session() as session:
        missing = find_missing_goalie_ids(session)

    print(f"Goalies in shot data with no players row: {len(missing)}")
    if not missing:
        print("Nothing to do.")
    else:
        totals = backfill(missing, dry_run=args.dry_run)
        print(
            f"\nDone: {totals['added']} added, "
            f"{totals['not_found']} not found, "
            f"{totals['non_goalie']} listed as non-goalies, "
            f"{totals['errors']} errors"
        )

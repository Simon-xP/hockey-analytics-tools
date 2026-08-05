"""Normalise franchise team IDs across the event-derived tables.

The NHL API reports different IDs for the same franchise depending on which
endpoint you ask. When Utah was renamed to the Mammoth for 2025-26, the
play-by-play and shift-chart endpoints began returning 68 while the
schedule, standings, and teams endpoints kept returning 59. The result is
one franchise split in two: `games` and `teams` say 59, `game_events`,
`player_shifts`, `shot_attempts`, and `game_advanced_stats` say 68.

Nothing errors. Joins just quietly return fewer rows, which showed up first
as goalie games with no win or loss assigned, because the goal events could
not be matched back to the team that scored them.

`src/ingest/nhl_api/client.py` now normalises at the ingest boundary via
`TEAM_ID_ALIASES`, so new data arrives clean. This repairs what is already
stored. It is idempotent and reads its mapping from the same constant, so
if the league does this again the fix is a one-line change there.

Usage:
    python -m scripts.repair_team_ids --dry-run
    python -m scripts.repair_team_ids
"""

import argparse

from sqlalchemy import text

from src.core.db import get_session, init_db
from src.ingest.nhl_api.client import TEAM_ID_ALIASES

# (table, column) pairs holding a team reference sourced from event data.
TEAM_COLUMNS = [
    ("game_events", "team_id"),
    ("player_shifts", "team_id"),
    ("shot_attempts", "team_id"),
    ("shot_attempts", "opponent_team_id"),
    ("game_advanced_stats", "team_id"),
    ("game_advanced_stats", "opponent_team_id"),
    ("goalie_game_log", "team_id"),
    ("goalie_game_log", "opponent_team_id"),
    ("games", "home_team_id"),
    ("games", "away_team_id"),
]


def audit(session) -> list[tuple[str, str, int, int]]:
    """Find rows still carrying an aliased team ID.

    Returns (table, column, stale_id, row_count).
    """
    findings = []
    for table, column in TEAM_COLUMNS:
        for stale_id in TEAM_ID_ALIASES:
            count = session.execute(
                text(f"SELECT count(*) FROM {table} WHERE {column} = :sid"),
                {"sid": stale_id},
            ).scalar()
            if count:
                findings.append((table, column, stale_id, count))
    return findings


# Tables where `opponent_team_id` was derived from `team_id` at build time.
# An aliased team_id does not just make its own column wrong, it silently
# poisons the derived opponent column with a valid-looking but incorrect
# team. That damage survives the alias fix, because the stored value is a
# real team ID, so it has to be recomputed from `games` separately.
# `goalie_game_log` is deliberately absent: both its team columns come from
# the same poisoned source, so there is no trustworthy side to recompute the
# other from. Rebuild it with `build_goalie_game_log --force` after this
# runs, once the shot table underneath it is correct.
DERIVED_OPPONENT_TABLES = [
    ("shot_attempts", "team_id", "opponent_team_id"),
    ("game_advanced_stats", "team_id", "opponent_team_id"),
]


def audit_opponents(session) -> list[tuple[str, int, int]]:
    """Rows whose opponent column disagrees with the schedule.

    Returns (table, row_count, game_count).
    """
    findings = []
    for table, own_col, opp_col in DERIVED_OPPONENT_TABLES:
        row = session.execute(
            text(f"""
                SELECT count(*), count(DISTINCT t.game_id)
                FROM {table} t JOIN games g ON g.game_id = t.game_id
                WHERE t.{opp_col} IS NOT NULL AND t.{own_col} IS NOT NULL
                  AND t.{opp_col} <> (CASE WHEN t.{own_col} = g.home_team_id
                                           THEN g.away_team_id
                                           ELSE g.home_team_id END)
            """)
        ).fetchone()
        if row and row[0]:
            findings.append((table, row[0], row[1]))
    return findings


def repair_opponents(session, dry_run: bool = False) -> int:
    """Recompute derived opponent columns from the schedule."""
    findings = audit_opponents(session)
    if not findings:
        print("  All derived opponent columns agree with the schedule.")
        return 0

    updated = 0
    table_map = {t: (own, opp) for t, own, opp in DERIVED_OPPONENT_TABLES}
    for table, rows, games in findings:
        own_col, opp_col = table_map[table]
        print(f"  {table}.{opp_col}: {rows} rows across {games} games "
              f"disagree with the schedule")
        updated += rows
        if dry_run:
            continue
        session.execute(
            text(f"""
                UPDATE {table} t
                SET {opp_col} = CASE WHEN t.{own_col} = g.home_team_id
                                     THEN g.away_team_id
                                     ELSE g.home_team_id END
                FROM games g
                WHERE g.game_id = t.game_id
                  AND t.{opp_col} IS NOT NULL AND t.{own_col} IS NOT NULL
                  AND t.{opp_col} <> (CASE WHEN t.{own_col} = g.home_team_id
                                           THEN g.away_team_id
                                           ELSE g.home_team_id END)
            """)
        )
    return updated


def repair(dry_run: bool = False) -> dict:
    totals = {"updated": 0, "tables": 0}

    with get_session() as session:
        findings = audit(session)

        if not findings:
            print("No aliased team IDs found.")

        for table, column, stale_id, count in findings:
            canonical = TEAM_ID_ALIASES[stale_id]
            print(f"  {table}.{column}: {count} rows with {stale_id} "
                  f"-> {canonical}")
            if dry_run:
                continue
            session.execute(
                text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old"),
                {"new": canonical, "old": stale_id},
            )
            totals["updated"] += count
            totals["tables"] += 1

        # Stage two: the collateral damage. Must run after the alias fix so
        # the recomputation compares against corrected team IDs.
        print("\nChecking derived opponent columns...")
        totals["opponents_fixed"] = repair_opponents(session, dry_run=dry_run)

        if not dry_run:
            session.commit()

    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Normalise aliased franchise team IDs"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    init_db()

    print(f"Aliases in effect: {TEAM_ID_ALIASES}")
    totals = repair(dry_run=args.dry_run)

    changed = totals["updated"] + totals.get("opponents_fixed", 0)
    if changed:
        print(f"\nDone: {totals['updated']} aliased IDs normalised, "
              f"{totals.get('opponents_fixed', 0)} derived opponent values "
              f"recomputed.")
        print("Rebuild anything derived from these rows, for example:")
        print("  python -m scripts.build_goalie_game_log --season 20252026 --force")
    else:
        print("\nNothing to repair.")

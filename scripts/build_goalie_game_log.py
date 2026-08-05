"""Build goalie_game_log from shot_attempts + player_shifts + games.

One row per goalie per game they played. Safe to re-run: existing games are
skipped unless --force is passed.

Usage:
    # All games that have shot data
    python -m scripts.build_goalie_game_log

    # One or more seasons
    python -m scripts.build_goalie_game_log --season 20242025 20252026

    # Rebuild a season from scratch (after a logic change)
    python -m scripts.build_goalie_game_log --season 20242025 --force

    # Single game, useful when checking a specific box score
    python -m scripts.build_goalie_game_log --game-id 2024020500
"""

import argparse

from sqlalchemy import text

from src.analytics.goalies.game_log import build_goalie_rows, known_goalie_ids
from src.core.db import get_session, init_db
from src.core.models import GoalieGameLog

# Columns copied straight across from the dataclass to the ORM row.
_PASSTHROUGH_FIELDS = (
    "game_id", "goalie_id", "team_id", "opponent_team_id", "game_date",
    "season", "is_start", "is_relief", "is_home", "toi_seconds",
    "played_full_game", "decision", "shutout", "team_score",
    "opponent_score", "shots_against", "saves", "goals_against",
    "fenwick_against", "empty_net_ga_team",
    "ev_shots_against", "ev_goals_against",
    "pk_shots_against", "pk_goals_against",
    "pp_shots_against", "pp_goals_against",
    "fpts",
)


def get_candidate_games(session, seasons: list[str] | None) -> list[dict]:
    """Games with shot data and a final score, oldest first."""
    where = ["g.home_score IS NOT NULL"]
    params: dict = {}

    if seasons:
        clauses = []
        for i, season in enumerate(seasons):
            start_year = int(season[:4])
            clauses.append(f"(g.game_id >= :s{i} AND g.game_id < :e{i})")
            params[f"s{i}"] = start_year * 1_000_000
            params[f"e{i}"] = (start_year + 1) * 1_000_000
        where.append("(" + " OR ".join(clauses) + ")")

    rows = session.execute(
        text(f"""
            SELECT g.game_id, g.home_team_id, g.away_team_id, g.date,
                   g.home_score, g.away_score
            FROM games g
            WHERE {' AND '.join(where)}
              AND EXISTS (
                  SELECT 1 FROM shot_attempts sa WHERE sa.game_id = g.game_id
              )
            ORDER BY g.game_id
        """),
        params,
    ).fetchall()

    return [
        {
            "game_id": r[0], "home_team_id": r[1], "away_team_id": r[2],
            "game_date": r[3], "home_score": r[4], "away_score": r[5],
        }
        for r in rows
    ]


def process_games(games: list[dict], force: bool = False) -> dict:
    totals = {"games": 0, "skipped": 0, "rows": 0, "starts": 0,
              "no_shifts": 0, "errors": 0}

    with get_session() as session:
        goalie_ids = known_goalie_ids(session)
        print(f"  Known goalies: {len(goalie_ids)}")

        existing = set(
            session.execute(
                text("SELECT DISTINCT game_id FROM goalie_game_log")
            ).scalars().all()
        )

        pending_commit = 0
        for i, game in enumerate(games):
            game_id = game["game_id"]

            if game_id in existing:
                if not force:
                    totals["skipped"] += 1
                    continue
                session.query(GoalieGameLog).filter(
                    GoalieGameLog.game_id == game_id
                ).delete(synchronize_session=False)

            try:
                rows = build_goalie_rows(
                    session,
                    game_id=game_id,
                    home_team_id=game["home_team_id"],
                    away_team_id=game["away_team_id"],
                    game_date=game["game_date"],
                    home_score=game["home_score"],
                    away_score=game["away_score"],
                    goalie_ids=goalie_ids,
                )
            except Exception as e:  # noqa: BLE001 - one bad game must not stop the run
                print(f"  Error on {game_id}: {e}")
                totals["errors"] += 1
                continue

            if not rows:
                totals["errors"] += 1
                continue

            # A game where nobody is flagged as a start means the shift data
            # is missing. Worth counting rather than silently accepting.
            if not any(r.is_start for r in rows):
                totals["no_shifts"] += 1

            for r in rows:
                session.add(GoalieGameLog(**{
                    f: getattr(r, f) for f in _PASSTHROUGH_FIELDS
                }))
                totals["rows"] += 1
                if r.is_start:
                    totals["starts"] += 1

            totals["games"] += 1
            pending_commit += 1

            if pending_commit >= 200:
                session.commit()
                pending_commit = 0

            if (i + 1) % 500 == 0 or i == len(games) - 1:
                print(
                    f"  Progress: {i + 1}/{len(games)} checked, "
                    f"{totals['games']} built, {totals['rows']} rows, "
                    f"{totals['errors']} errors",
                    flush=True,
                )

    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build the per-goalie per-game stat log"
    )
    parser.add_argument("--season", nargs="+", help="Season(s), e.g. 20242025")
    parser.add_argument("--game-id", type=int, help="Single game ID")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild games that are already in the table")
    args = parser.parse_args()
    init_db()

    with get_session() as session:
        if args.game_id:
            season = f"{args.game_id // 1_000_000}{args.game_id // 1_000_000 + 1}"
            games = [
                g for g in get_candidate_games(session, [season])
                if g["game_id"] == args.game_id
            ]
        else:
            games = get_candidate_games(session, args.season)

    print(f"Candidate games: {len(games)}")
    if not games:
        print("Nothing to do.")
    else:
        totals = process_games(games, force=args.force)
        print(
            f"\nDone: {totals['games']} games built, {totals['rows']} rows "
            f"({totals['starts']} starts), {totals['skipped']} skipped, "
            f"{totals['no_shifts']} games with no start detected, "
            f"{totals['errors']} errors"
        )

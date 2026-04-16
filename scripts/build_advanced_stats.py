"""Compute game_advanced_stats from game_events + player_shifts.

Runs the shift-event correlation engine to produce per-player, per-game,
per-situation advanced stats. Safe to re-run — skips already-processed games.

Usage:
    # Process all ingested games
    python -m scripts.build_advanced_stats

    # Process specific season
    python -m scripts.build_advanced_stats --season 20252026

    # Process single game
    python -m scripts.build_advanced_stats --game-id 2025021246
"""

import argparse

from sqlalchemy import text

from src.core.db import get_session, init_db
from src.core.models import Game, GameAdvancedStats
from src.tools.advanced_stats.correlate import compute_game_advanced_stats


def get_ingested_game_ids(session) -> set[int]:
    rows = session.execute(
        text("SELECT DISTINCT game_id FROM game_events")
    ).fetchall()
    return {r[0] for r in rows}


def get_processed_game_ids(session) -> set[int]:
    rows = session.execute(
        text("SELECT DISTINCT game_id FROM game_advanced_stats")
    ).fetchall()
    return {r[0] for r in rows}


def process_games(game_ids: list[int]) -> dict:
    totals = {"games": 0, "skipped": 0, "rows": 0, "errors": 0}

    with get_session() as session:
        already_done = get_processed_game_ids(session)

        # Preload game info
        game_info = {}
        games = session.query(Game).filter(Game.game_id.in_(game_ids)).all()
        for g in games:
            game_info[g.game_id] = {
                "home_team_id": g.home_team_id,
                "away_team_id": g.away_team_id,
            }

        batch_count = 0
        for i, game_id in enumerate(game_ids):
            if game_id in already_done:
                totals["skipped"] += 1
                continue

            info = game_info.get(game_id)
            if not info:
                totals["errors"] += 1
                continue

            try:
                nested = session.begin_nested()
                stats, player_team_map = compute_game_advanced_stats(
                    session, game_id,
                    home_team_id=info["home_team_id"],
                    away_team_id=info["away_team_id"],
                )

                # Write to DB
                rows_added = 0
                for (player_id, situation), st in stats.items():
                    # Skip players with zero TOI (shouldn't happen but safety check)
                    if st.toi_seconds <= 0 and situation != "all":
                        continue

                    team_id = player_team_map.get(player_id)
                    opp_team_id = None
                    if team_id:
                        opp_team_id = (
                            info["away_team_id"] if team_id == info["home_team_id"]
                            else info["home_team_id"]
                        )

                    row = GameAdvancedStats(
                        game_id=game_id,
                        player_id=player_id,
                        team_id=team_id,
                        opponent_team_id=opp_team_id,
                        situation=situation,
                        toi_seconds=st.toi_seconds,
                        goals=st.goals,
                        assists=st.assists,
                        first_assists=st.first_assists,
                        second_assists=st.second_assists,
                        points=st.points,
                        shots=st.shots,
                        shot_attempts=st.shot_attempts,
                        missed_shots=st.missed_shots,
                        blocked_shots=st.blocked_shots,
                        hits=st.hits,
                        hits_taken=st.hits_taken,
                        blocks=st.blocks,
                        giveaways=st.giveaways,
                        takeaways=st.takeaways,
                        penalties=st.penalties,
                        penalties_drawn=st.penalties_drawn,
                        faceoff_wins=st.faceoff_wins,
                        faceoff_losses=st.faceoff_losses,
                        ixg=st.ixg,
                        cf=st.cf, ca=st.ca,
                        ff=st.ff, fa=st.fa,
                        sf=st.sf, sa=st.sa,
                        gf=st.gf, ga=st.ga,
                        xgf=st.xgf, xga=st.xga,
                        scf=st.scf, sca=st.sca,
                        hdcf=st.hdcf, hdca=st.hdca,
                        oz_starts=st.oz_starts,
                        dz_starts=st.dz_starts,
                        nz_starts=st.nz_starts,
                        ipp=st.ipp,
                    )
                    session.add(row)
                    rows_added += 1

                nested.commit()
                totals["games"] += 1
                totals["rows"] += rows_added
                batch_count += 1

            except Exception as e:
                nested.rollback()
                print(f"  Error processing game {game_id}: {e}")
                totals["errors"] += 1
                continue

            if batch_count >= 50:
                session.commit()
                batch_count = 0

            done = totals["games"]
            if done % 25 == 0 or i == len(game_ids) - 1:
                print(
                    f"  Progress: {i + 1}/{len(game_ids)} checked, "
                    f"{done} processed, {totals['rows']} rows, "
                    f"{totals['errors']} errors",
                    flush=True,
                )

    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compute advanced stats from game events + shifts"
    )
    parser.add_argument("--season", nargs="+", help="Season(s) to process")
    parser.add_argument("--game-id", type=int, help="Single game ID")
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
        print(f"\nDone: {totals['games']} games, {totals['rows']} stat rows, "
              f"{totals['skipped']} skipped, {totals['errors']} errors")

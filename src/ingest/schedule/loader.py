"""Load NHL schedule from CSV into the games table."""

import pandas as pd
from datetime import datetime
from pathlib import Path

from src.core.db import get_session
from src.core.models import Game, Team


DATA_DIR = Path(__file__).parents[3] / "data" / "raw"
SCHEDULE_CSV = DATA_DIR / "nhl-schedule-raw.csv"

# Team ID mappings for discrepancies between schedule CSV and NHL API
# The schedule CSV uses different IDs for some teams
TEAM_ID_MAP = {
    68: 59,  # Utah Hockey Club: CSV uses 68, NHL API uses 59
}


def load_schedule(csv_path: Path = SCHEDULE_CSV) -> int:
    """
    Load NHL schedule CSV into games table.

    The CSV has one row per team per game, so each game appears twice.
    We deduplicate and determine home/away from the 'away' column.

    Returns number of games inserted.
    """
    df = pd.read_csv(csv_path)

    # Group by gameId to get both teams per game
    games_grouped = df.groupby("gameId")

    count = 0
    skipped = 0

    with get_session() as session:
        # Build team_id lookup from database
        teams_by_id = {t.team_id: t for t in session.query(Team).all()}

        for game_id, group in games_grouped:
            # Check if game already exists
            existing = session.query(Game).filter(Game.game_id == game_id).first()
            if existing:
                skipped += 1
                continue

            # Find home and away teams from the group
            home_team_id = None
            away_team_id = None

            for _, row in group.iterrows():
                team_id = int(row["teamId"])
                # Apply team ID mapping if needed
                team_id = TEAM_ID_MAP.get(team_id, team_id)
                is_away = row["away"] == "@"

                if is_away:
                    away_team_id = team_id
                else:
                    home_team_id = team_id

            if not home_team_id or not away_team_id:
                print(f"Warning: Could not determine home/away for game {game_id}")
                continue

            # Verify teams exist in database
            if home_team_id not in teams_by_id:
                print(f"Warning: Home team {home_team_id} not in database for game {game_id}")
                continue
            if away_team_id not in teams_by_id:
                print(f"Warning: Away team {away_team_id} not in database for game {game_id}")
                continue

            # Get date and time from first row
            first_row = group.iloc[0]
            game_date = pd.to_datetime(first_row["date"]).date()

            # Parse start time
            start_time_utc = None
            if pd.notna(first_row["gameStartUTC"]):
                try:
                    start_time_utc = pd.to_datetime(first_row["gameStartUTC"])
                except Exception:
                    pass

            # Get yahoo week
            yahoo_week = int(first_row["yahooWk"]) if pd.notna(first_row["yahooWk"]) else None

            game = Game(
                game_id=int(game_id),
                date=game_date,
                start_time_utc=start_time_utc,
                home_team_id=home_team_id,
                away_team_id=away_team_id,
                yahoo_week=yahoo_week,
            )
            session.add(game)
            count += 1

        print(f"Inserted {count} games ({skipped} already existed)")

    return count


if __name__ == "__main__":
    count = load_schedule()
    print(f"Done! Loaded {count} games.")

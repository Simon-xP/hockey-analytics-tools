"""Schedule provider — game schedule queries.

Schedule data is public knowledge (future games are announced in advance),
so no temporal gating is needed. This provider exists for consistency
with the BacktestDataContext interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session


def _season_game_id_range(season: str) -> tuple[int, int]:
    prefix = int(season[:4])
    return prefix * 1_000_000 + 20_000, prefix * 1_000_000 + 30_000


@dataclass
class ScheduleProvider:
    session: Session
    as_of: date

    def get_week_date_range(self, yahoo_week: int) -> tuple[date, date] | None:
        """Get (monday, sunday) for a Yahoo fantasy week.

        Returns None if no games exist in that week.
        """
        from src.core.models import Game

        game = (
            self.session.query(Game)
            .filter(Game.yahoo_week == yahoo_week)
            .order_by(Game.date)
            .first()
        )
        if not game:
            return None

        game_date = game.date
        days_since_monday = game_date.weekday()
        monday = game_date - timedelta(days=days_since_monday)
        sunday = monday + timedelta(days=6)
        return monday, sunday

    def get_team_games_in_range(
        self,
        team_id: int,
        start_date: date,
        end_date: date,
    ) -> list[dict]:
        """Get games for a team in a date range."""
        rows = self.session.execute(
            text("""
                SELECT game_id, date, home_team_id, away_team_id,
                       home_score, away_score, yahoo_week
                FROM games
                WHERE (home_team_id = :tid OR away_team_id = :tid)
                  AND date >= :start AND date <= :end
                ORDER BY date
            """),
            {"tid": team_id, "start": start_date, "end": end_date},
        ).fetchall()

        return [
            {
                "game_id": r.game_id,
                "date": r.date,
                "home_team_id": r.home_team_id,
                "away_team_id": r.away_team_id,
                "home_score": r.home_score,
                "away_score": r.away_score,
                "yahoo_week": r.yahoo_week,
            }
            for r in rows
        ]

    def get_season_week_dates(self, season: str) -> list[tuple[date, date]]:
        """Return (monday, sunday) tuples for each fantasy week in a season.

        Skips weeks with no games.
        """
        gid_min, gid_max = _season_game_id_range(season)

        bounds = self.session.execute(
            text("""
                SELECT MIN(date) AS first_date, MAX(date) AS last_date
                FROM games
                WHERE game_id > :gid_min AND game_id < :gid_max
            """),
            {"gid_min": gid_min, "gid_max": gid_max},
        ).first()

        if not bounds or not bounds.first_date:
            return []

        game_dates_rows = self.session.execute(
            text("""
                SELECT DISTINCT date FROM games
                WHERE game_id > :gid_min AND game_id < :gid_max
                ORDER BY date
            """),
            {"gid_min": gid_min, "gid_max": gid_max},
        ).fetchall()
        game_dates = {row.date for row in game_dates_rows}

        monday = bounds.first_date - timedelta(days=bounds.first_date.weekday())

        weeks = []
        while monday <= bounds.last_date:
            sunday = monday + timedelta(days=6)
            if any(monday <= d <= sunday for d in game_dates):
                weeks.append((monday, sunday))
            monday = sunday + timedelta(days=1)

        return weeks

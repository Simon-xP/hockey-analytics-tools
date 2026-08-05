"""Stats provider — temporal-gated access to player performance data.

Wraps GameAdvancedStats queries with a mandatory as_of cutoff.
All queries use strict less-than (Game.date < as_of) so a decision
on day D cannot see games played on D itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.scoring import SKATER_WEIGHTS

_FPTS_EXPR = (
    f"gas.goals * {SKATER_WEIGHTS['goals']} + "
    f"gas.assists * {SKATER_WEIGHTS['assists']} + "
    f"gas.penalties * 2.0 * {SKATER_WEIGHTS['pim']} + "
    f"gas.shots * {SKATER_WEIGHTS['shots']} + "
    f"gas.hits * {SKATER_WEIGHTS['hits']} + "
    f"gas.blocks * {SKATER_WEIGHTS['blocks']}"
)


@dataclass
class StatsProvider:
    """Temporal-gated player stats access.

    Every query filters Game.date < as_of — no exceptions.
    """

    session: Session
    as_of: date

    def get_player_fpts_per_gp(
        self,
        nhl_id: int,
        season: str | None = None,
        lookback_days: int | None = None,
        min_gp: int = 1,
    ) -> dict | None:
        """Get a player's FPTS/GP using games before as_of.

        Args:
            nhl_id: NHL player ID.
            season: Season string like "20252026". If given, restricts to
                    that season's game_id range.
            lookback_days: If given, only include games within this many days
                          before as_of. If None, uses season or all data.
            min_gp: Minimum games played to return a result.

        Returns:
            {"fpts_per_gp": float, "gp": int, "total_fpts": float} or None.
        """
        conditions = [
            "gas.situation = 'all'",
            "gas.toi_seconds > 0",
            "g.date < :as_of",
            "gas.player_id = :nhl_id",
        ]
        params: dict = {"as_of": self.as_of, "nhl_id": nhl_id}

        if season:
            prefix = int(season[:4])
            conditions.append(f"gas.game_id > {prefix * 1_000_000 + 20_000}")
            conditions.append(f"gas.game_id < {prefix * 1_000_000 + 30_000}")

        if lookback_days:
            window_start = self.as_of - timedelta(days=lookback_days)
            conditions.append("g.date >= :window_start")
            params["window_start"] = window_start

        where = " AND ".join(conditions)
        row = self.session.execute(
            text(f"""
                SELECT COUNT(*) AS gp, COALESCE(SUM({_FPTS_EXPR}), 0) AS total_fpts
                FROM game_advanced_stats gas
                JOIN games g ON gas.game_id = g.game_id
                WHERE {where}
            """),
            params,
        ).first()

        if not row or row.gp < min_gp:
            return None

        return {
            "fpts_per_gp": round(float(row.total_fpts) / row.gp, 4),
            "gp": row.gp,
            "total_fpts": round(float(row.total_fpts), 2),
        }

    def get_trailing_rankings(
        self,
        lookback_days: int = 30,
        min_gp: int = 5,
    ) -> list[dict]:
        """Rank all skaters by trailing FPTS/GP.

        Returns list sorted by fpts_per_gp descending:
            {"nhl_id", "name", "position", "fpts_per_gp", "gp", "total_fpts"}
        """
        window_start = self.as_of - timedelta(days=lookback_days)

        rows = self.session.execute(
            text(f"""
                SELECT
                    gas.player_id AS nhl_id,
                    p.full_name AS name,
                    p.position,
                    COUNT(*) AS gp,
                    SUM({_FPTS_EXPR}) AS total_fpts
                FROM game_advanced_stats gas
                JOIN games g ON gas.game_id = g.game_id
                JOIN players p ON gas.player_id = p.nhl_id
                WHERE gas.situation = 'all'
                  AND gas.toi_seconds > 0
                  AND g.date >= :window_start
                  AND g.date < :as_of
                  AND p.position != 'G'
                GROUP BY gas.player_id, p.full_name, p.position
                HAVING COUNT(*) >= :min_gp
                ORDER BY SUM({_FPTS_EXPR}) / COUNT(*) DESC
            """),
            {"window_start": window_start, "as_of": self.as_of, "min_gp": min_gp},
        ).fetchall()

        return [
            {
                "nhl_id": row.nhl_id,
                "name": row.name,
                "position": row.position,
                "gp": row.gp,
                "total_fpts": round(float(row.total_fpts), 2),
                "fpts_per_gp": round(float(row.total_fpts) / row.gp, 4),
            }
            for row in rows
        ]

    def get_replacement_level(
        self,
        n_rostered: int = 160,
        lookback_days: int = 30,
    ) -> dict:
        """Compute replacement-level FPTS/GP by position group.

        Takes the band from rank n_rostered to n_rostered+20 and splits
        by forward/defense.

        Returns:
            {"forward": float, "defense": float, "replacement_fpts_per_gp": float}
        """
        all_players = self.get_trailing_rankings(
            lookback_days=lookback_days, min_gp=5
        )

        band = all_players[n_rostered : n_rostered + 20]

        forward_rates = []
        defense_rates = []
        for p in band:
            if p["position"] == "D":
                defense_rates.append(p["fpts_per_gp"])
            else:
                forward_rates.append(p["fpts_per_gp"])

        all_rates = [p["fpts_per_gp"] for p in band]
        overall = sum(all_rates) / len(all_rates) if all_rates else 0.0

        return {
            "forward": sum(forward_rates) / len(forward_rates) if forward_rates else overall,
            "defense": sum(defense_rates) / len(defense_rates) if defense_rates else overall,
            "replacement_fpts_per_gp": overall,
        }

    def get_actual_fpts_in_range(
        self,
        nhl_id: int,
        start_date: date,
        end_date: date,
    ) -> dict:
        """Get actual FPTS a player produced in a date range (inclusive).

        This is for outcome evaluation — intentionally does NOT
        respect as_of, since we need actual results after the decision.
        """
        row = self.session.execute(
            text(f"""
                SELECT
                    COUNT(*) AS gp,
                    COALESCE(SUM({_FPTS_EXPR}), 0) AS total_fpts
                FROM game_advanced_stats gas
                JOIN games g ON gas.game_id = g.game_id
                WHERE gas.player_id = :nhl_id
                  AND gas.situation = 'all'
                  AND gas.toi_seconds > 0
                  AND g.date >= :start_date
                  AND g.date <= :end_date
            """),
            {"nhl_id": nhl_id, "start_date": start_date, "end_date": end_date},
        ).first()

        return {
            "gp": row.gp if row else 0,
            "total_fpts": round(float(row.total_fpts), 2) if row else 0.0,
        }

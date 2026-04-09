"""Shared helpers for computing player stats across API endpoints."""

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.models import GameIndividualStats
from src.tools.fantasy.scoring import SKATER_WEIGHTS


def compute_fpts_per_gp(
    session: Session,
    nhl_id: int,
    season: str = "20242025",
    situation: str = "all",
) -> dict | None:
    """Compute fantasy points per game and avg TOI for a player.

    Returns dict with fpts_per_gp, avg_toi, stats_per_gp, or None if no data.
    """
    stats = (
        session.query(
            func.count().label("gp"),
            func.avg(GameIndividualStats.goals_per_60).label("avg_goals"),
            func.avg(GameIndividualStats.total_assists_per_60).label("avg_assists"),
            func.avg(GameIndividualStats.shots_per_60).label("avg_shots"),
            func.avg(GameIndividualStats.hits_per_60).label("avg_hits"),
            func.avg(GameIndividualStats.shots_blocked_per_60).label("avg_blocks"),
            func.avg(GameIndividualStats.pim_per_60).label("avg_pim"),
            func.avg(GameIndividualStats.toi).label("avg_toi"),
            func.avg(GameIndividualStats.ixg_per_60).label("avg_ixg"),
        )
        .filter(
            GameIndividualStats.nhl_id == nhl_id,
            GameIndividualStats.season == season,
            GameIndividualStats.situation == situation,
        )
        .first()
    )

    if not stats or not stats.avg_toi or stats.avg_toi <= 0:
        return None

    toi_frac = stats.avg_toi / 60.0
    fpts = 0.0
    per_gp = {}

    for val, cat in [
        (stats.avg_goals, "goals"),
        (stats.avg_assists, "assists"),
        (stats.avg_shots, "shots"),
        (stats.avg_hits, "hits"),
        (stats.avg_blocks, "blocks"),
        (stats.avg_pim, "pim"),
    ]:
        if val is not None:
            pg = val * toi_frac
            fpts += pg * SKATER_WEIGHTS[cat]
            per_gp[cat] = round(pg, 2)

    return {
        "gp": stats.gp,
        "fpts_per_gp": round(fpts, 2),
        "avg_toi": round(stats.avg_toi, 1),
        "stats_per_gp": per_gp,
    }

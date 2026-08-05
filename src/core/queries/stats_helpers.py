"""Shared helpers for computing player stats across API endpoints."""

from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.models import Game, GameIndividualStats
from src.core.models.advanced_stats import GameAdvancedStats
from src.core.scoring import SKATER_WEIGHTS


def compute_fpts_per_gp(
    session: Session,
    nhl_id: int,
    season: str = "20242025",
    situation: str = "all",
    as_of: Optional[date] = None,
) -> dict | None:
    """Compute fantasy points per game and avg TOI for a player.

    Tries GameAdvancedStats first (NHL API data, has 2025-26), falls
    back to GameIndividualStats (NST data, has 2023-24 and 2024-25).

    If `as_of` is provided, only games with `Game.date < as_of` are
    considered. The strict less-than is deliberate: a decision made on
    day D must not see D's own games (enforces the decision deadline).

    Returns dict with fpts_per_gp, avg_toi, stats_per_gp, or None if no data.
    """
    # Try GameAdvancedStats first (raw counts, 2025-26 data available)
    result = _fpts_from_advanced_stats(session, nhl_id, season, situation, as_of)
    if result is not None:
        return result

    # Fall back to GameIndividualStats (per-60 rates, older seasons)
    return _fpts_from_individual_stats(session, nhl_id, season, situation, as_of)


def _fpts_from_advanced_stats(
    session: Session,
    nhl_id: int,
    season: str,
    situation: str,
    as_of: Optional[date] = None,
) -> dict | None:
    """Compute FPTS/GP from GameAdvancedStats (raw counts per game).

    GameAdvancedStats uses player_id (not nhl_id) and stores raw counts
    + toi_seconds. We compute per-game averages directly.
    """
    # Map season string to game_id range (e.g., "20252026" → 2025020001-2025029999)
    season_prefix = int(season[:4])
    game_id_min = season_prefix * 1_000_000 + 20_000
    game_id_max = season_prefix * 1_000_000 + 30_000

    query = (
        session.query(
            func.count().label("gp"),
            func.avg(GameAdvancedStats.goals).label("avg_goals"),
            func.avg(GameAdvancedStats.assists).label("avg_assists"),
            func.avg(GameAdvancedStats.shots).label("avg_shots"),
            func.avg(GameAdvancedStats.hits).label("avg_hits"),
            func.avg(GameAdvancedStats.blocks).label("avg_blocks"),
            func.avg(GameAdvancedStats.penalties).label("avg_penalties"),
            func.avg(GameAdvancedStats.toi_seconds).label("avg_toi_seconds"),
            func.avg(GameAdvancedStats.ixg).label("avg_ixg"),
        )
        .filter(
            GameAdvancedStats.player_id == nhl_id,
            GameAdvancedStats.situation == situation,
            GameAdvancedStats.game_id > game_id_min,
            GameAdvancedStats.game_id < game_id_max,
            GameAdvancedStats.toi_seconds > 0,
        )
    )

    if as_of is not None:
        query = query.join(Game, GameAdvancedStats.game_id == Game.game_id).filter(
            Game.date < as_of
        )

    stats = query.first()

    if not stats or not stats.gp or stats.gp == 0 or not stats.avg_toi_seconds:
        return None

    avg_toi_minutes = stats.avg_toi_seconds / 60.0
    if avg_toi_minutes <= 0:
        return None

    fpts = 0.0
    per_gp = {}

    # These are already per-game averages (raw counts averaged across games)
    for val, cat in [
        (stats.avg_goals, "goals"),
        (stats.avg_assists, "assists"),
        (stats.avg_shots, "shots"),
        (stats.avg_hits, "hits"),
        (stats.avg_blocks, "blocks"),
    ]:
        if val is not None:
            v = float(val)
            fpts += v * SKATER_WEIGHTS[cat]
            per_gp[cat] = round(v, 2)

    # PIM: penalties × 2 minutes is a rough approximation
    # GameAdvancedStats tracks penalty count, not minutes
    if stats.avg_penalties is not None:
        pim_approx = float(stats.avg_penalties) * 2.0
        fpts += pim_approx * SKATER_WEIGHTS["pim"]
        per_gp["pim"] = round(pim_approx, 2)

    return {
        "gp": stats.gp,
        "fpts_per_gp": round(fpts, 2),
        "avg_toi": round(avg_toi_minutes, 1),
        "stats_per_gp": per_gp,
    }


def _fpts_from_individual_stats(
    session: Session,
    nhl_id: int,
    season: str,
    situation: str,
    as_of: Optional[date] = None,
) -> dict | None:
    """Compute FPTS/GP from GameIndividualStats (per-60 rates).

    Legacy path for NST data (2023-24, 2024-25).
    """
    query = (
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
    )

    if as_of is not None:
        query = query.filter(GameIndividualStats.game_date < as_of)

    stats = query.first()

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

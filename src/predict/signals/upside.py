"""Upside model — quantify a player's individual talent ceiling.

Returns a score in [-1.0, 1.0]:
    Positive = underperforming their underlying talent (buy candidate)
    Negative = overperforming their talent (sell/regression risk)
    ~0 = performing as expected

Upside is distinct from opportunity. Upside measures individual talent
("how good CAN this player be based on their own skill?"). Opportunity
measures current situational favorability ("how good is their environment
RIGHT NOW?"). See docs/upside-and-opportunity.md for the full framework.

Upside signals should persist across weeks and into the next season — a
player with genuine upside is underperforming their talent, and that talent
doesn't evaporate when the situation changes.

Sources everything from `GameAdvancedStats` (joined to `Game` for the
date filter). Every query respects `as_of: Optional[date]` with a
strict-less-than cutoff (`Game.date < as_of`) so backtests don't leak.
"""

from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.models import Game, GameAdvancedStats


SEASON_START_DEFAULT = date(2025, 10, 1)

_MIN_GP = 15


def compute_upside_score(
    session: Session,
    nhl_id: int,
    as_of: Optional[date] = None,
    season_start: date = SEASON_START_DEFAULT,
) -> float:
    """Estimate a player's talent-based upside in [-1.0, 1.0].

    Components (each clamped and summed):
    1. Shooting luck: goals vs ixG — underperforming xG means regression up
    2. Process vs results: on-ice xGF% vs actual scoring — strong underlying
       play not yet reflected in points
    """
    shooting = _shooting_luck_score(session, nhl_id, as_of, season_start)
    process = _process_vs_results_score(session, nhl_id, as_of, season_start)

    total = shooting + process
    return max(-1.0, min(1.0, round(total, 3)))


def compute_upside_breakdown(
    session: Session,
    nhl_id: int,
    as_of: Optional[date] = None,
    season_start: date = SEASON_START_DEFAULT,
) -> dict[str, float]:
    """Return individual upside components for debugging/display."""
    return {
        "shooting_luck": _shooting_luck_score(session, nhl_id, as_of, season_start),
        "process_vs_results": _process_vs_results_score(
            session, nhl_id, as_of, season_start
        ),
    }


def hold_patience_games(
    upside_score: float,
    fpts_below_replacement: float,
) -> int:
    """How many games to hold an underperforming player with upside.

    Higher upside = more patience. Further below replacement = less patience.
    """
    if upside_score <= 0:
        return 0

    base_patience = int(upside_score * 20)
    urgency = max(0, int(fpts_below_replacement * 5))
    return max(0, base_patience - urgency)


# =========================================================================
# Helpers
# =========================================================================


def _apply_cutoff(query, as_of: Optional[date], season_start: date):
    """Join GameAdvancedStats to Game and apply the leakage-safe date window."""
    query = query.join(Game, GameAdvancedStats.game_id == Game.game_id).filter(
        Game.date >= season_start
    )
    if as_of is not None:
        query = query.filter(Game.date < as_of)
    return query


# =========================================================================
# Component scorers
# =========================================================================


def _shooting_luck_score(
    session: Session,
    nhl_id: int,
    as_of: Optional[date],
    season_start: date,
) -> float:
    """Compare actual goals to expected goals (ixG) across all situations.

    goals << ixG → regression up likely (positive upside).
    goals >> ixG → overperforming (negative upside).

    Future improvement: EB-shrunk career shooting baseline so vets who
    consistently over/underperform xG are judged against their personal
    rate rather than the league prior. Requires deeper historical data
    ingestion (pre-2021 seasons).
    """
    q = session.query(
        func.count(GameAdvancedStats.id).label("gp"),
        func.sum(GameAdvancedStats.goals).label("total_goals"),
        func.sum(GameAdvancedStats.ixg).label("total_ixg"),
    ).filter(
        GameAdvancedStats.player_id == nhl_id,
        GameAdvancedStats.situation == "all",
    )
    stats = _apply_cutoff(q, as_of, season_start).first()

    if not stats or stats.gp is None or stats.gp < _MIN_GP:
        return 0.0
    if stats.total_ixg is None or stats.total_goals is None:
        return 0.0
    if stats.total_ixg <= 0:
        return 0.0

    goals_per_game = float(stats.total_goals) / stats.gp
    ixg_per_game = float(stats.total_ixg) / stats.gp
    goals_over_expected = goals_per_game - ixg_per_game

    # -0.15 goals/game below expected → +0.3 upside
    return max(-0.3, min(0.3, -goals_over_expected * 2.0))


def _process_vs_results_score(
    session: Session,
    nhl_id: int,
    as_of: Optional[date],
    season_start: date,
) -> float:
    """Compare on-ice xGF% (process) to points per game (results).

    Strong process + weak results = unlucky, positive upside.
    Weak process + strong results = regression risk.
    """
    q = session.query(
        func.count(GameAdvancedStats.id).label("gp"),
        func.sum(GameAdvancedStats.xgf).label("total_xgf"),
        func.sum(GameAdvancedStats.xga).label("total_xga"),
        func.sum(GameAdvancedStats.goals).label("total_goals"),
        func.sum(GameAdvancedStats.assists).label("total_assists"),
    ).filter(
        GameAdvancedStats.player_id == nhl_id,
        GameAdvancedStats.situation == "5v5",
    )
    stats = _apply_cutoff(q, as_of, season_start).first()

    if not stats or stats.gp is None or stats.gp < _MIN_GP:
        return 0.0
    xgf = float(stats.total_xgf or 0)
    xga = float(stats.total_xga or 0)
    if xgf + xga <= 0:
        return 0.0

    xgf_pct = xgf / (xgf + xga) * 100.0
    xgf_quality = (xgf_pct - 50.0) / 100.0

    pts = float((stats.total_goals or 0) + (stats.total_assists or 0))
    pts_per_game = pts / stats.gp

    if xgf_quality > 0 and pts_per_game < 0.5:
        return min(0.2, xgf_quality * 0.5)
    if xgf_quality < -0.05 and pts_per_game > 0.8:
        return max(-0.2, xgf_quality * 0.5)
    return max(-0.15, min(0.15, xgf_quality * 0.3))

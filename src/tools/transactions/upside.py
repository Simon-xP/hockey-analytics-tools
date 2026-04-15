"""Upside model — quantify how much better a player could get.

Returns a score in [-1.0, 1.0]:
    Positive = underperforming their underlying talent (buy candidate)
    Negative = overperforming their talent (sell/regression risk)
    ~0 = performing as expected

Uses GameIndividualStats (ixg_per_60, goals_per_60, toi) and
GameOnIceStats (xgf_per_60, xga_per_60) — no reliance on missing
GameAdvancedStats.

This is explicitly a grey area that will be fine-tuned via backtesting.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.models import GameIndividualStats, GameOnIceStats


def compute_upside_score(
    session: Session,
    nhl_id: int,
    season: str = "20252026",
    situation: str = "all",
) -> float:
    """Estimate a player's upside.

    Components (each scaled to ~[-0.3, 0.3] and summed):
    1. Shooting luck: goals vs ixG — goals << ixG = positive upside
    2. TOI trend: recent TOI > season avg = coach trust increasing
    3. Process vs results: high on-ice xGF% with low scoring = unlucky

    Returns [-1.0, 1.0] clamped.
    """
    shooting = _shooting_luck_score(session, nhl_id, season, situation)
    toi_trend = _toi_trend_score(session, nhl_id, season, situation)
    process = _process_vs_results_score(session, nhl_id, season, situation)

    total = shooting + toi_trend + process
    return max(-1.0, min(1.0, round(total, 3)))


def compute_upside_breakdown(
    session: Session,
    nhl_id: int,
    season: str = "20252026",
    situation: str = "all",
) -> dict[str, float]:
    """Return individual upside components for debugging/display."""
    return {
        "shooting_luck": _shooting_luck_score(session, nhl_id, season, situation),
        "toi_trend": _toi_trend_score(session, nhl_id, season, situation),
        "process_vs_results": _process_vs_results_score(session, nhl_id, season, situation),
    }


def hold_patience_games(
    upside_score: float,
    fpts_below_replacement: float,
) -> int:
    """How many games to hold an underperforming player with upside.

    Higher upside = more patience. Further below replacement = less patience.

    Args:
        upside_score: from compute_upside_score()
        fpts_below_replacement: how far below replacement level (negative means above)

    Returns:
        Number of games to wait before dropping. 0 = drop now.
    """
    if upside_score <= 0:
        return 0  # no upside, drop immediately if below replacement

    # Base patience: 0.5 upside → 10 games, 1.0 upside → 20 games
    base_patience = int(upside_score * 20)

    # Urgency: further below replacement → less patience
    # 1.0 FPTS/GP below replacement → -5 games patience
    urgency = max(0, int(fpts_below_replacement * 5))

    return max(0, base_patience - urgency)


# =========================================================================
# Component scorers
# =========================================================================


def _shooting_luck_score(
    session: Session,
    nhl_id: int,
    season: str,
    situation: str,
    min_gp: int = 15,
) -> float:
    """Compare actual goals to expected goals (ixG).

    If goals << ixG, player is shooting below expected → positive upside
    (regression up likely).
    If goals >> ixG, they're overperforming → negative upside (sell signal).

    Uses per-60 rates × TOI to get per-game counts, then compares.
    """
    stats = (
        session.query(
            func.count().label("gp"),
            func.avg(GameIndividualStats.goals_per_60).label("avg_goals_per_60"),
            func.avg(GameIndividualStats.ixg_per_60).label("avg_ixg_per_60"),
            func.avg(GameIndividualStats.toi).label("avg_toi"),
        )
        .filter(
            GameIndividualStats.nhl_id == nhl_id,
            GameIndividualStats.season == season,
            GameIndividualStats.situation == situation,
        )
        .first()
    )

    if (
        not stats
        or stats.gp < min_gp
        or stats.avg_ixg_per_60 is None
        or stats.avg_goals_per_60 is None
        or stats.avg_toi is None
        or stats.avg_toi <= 0
    ):
        return 0.0

    toi_frac = stats.avg_toi / 60.0
    goals_per_game = stats.avg_goals_per_60 * toi_frac
    ixg_per_game = stats.avg_ixg_per_60 * toi_frac

    # goals_over_expected: negative means underperforming (positive upside)
    goals_over_expected = goals_per_game - ixg_per_game

    # Scale: -0.15 goals/game below expected = +0.3 upside
    return max(-0.3, min(0.3, -goals_over_expected * 2.0))


def _toi_trend_score(
    session: Session,
    nhl_id: int,
    season: str,
    situation: str,
    recent_window: int = 10,
    min_gp: int = 15,
) -> float:
    """Compare recent TOI to season average.

    Recent TOI > season avg = coach giving more trust (positive upside).
    Recent TOI < season avg = losing ice time (negative upside).
    """
    # Season average TOI
    season_avg = (
        session.query(func.avg(GameIndividualStats.toi))
        .filter(
            GameIndividualStats.nhl_id == nhl_id,
            GameIndividualStats.season == season,
            GameIndividualStats.situation == situation,
        )
        .scalar()
    )

    if season_avg is None or season_avg <= 0:
        return 0.0

    # Count total games
    total_gp = (
        session.query(func.count())
        .filter(
            GameIndividualStats.nhl_id == nhl_id,
            GameIndividualStats.season == season,
            GameIndividualStats.situation == situation,
        )
        .scalar()
    )

    if total_gp < min_gp:
        return 0.0

    # Recent window average TOI
    recent_stats = (
        session.query(GameIndividualStats.toi)
        .filter(
            GameIndividualStats.nhl_id == nhl_id,
            GameIndividualStats.season == season,
            GameIndividualStats.situation == situation,
            GameIndividualStats.toi.isnot(None),
        )
        .order_by(GameIndividualStats.game_date.desc())
        .limit(recent_window)
        .all()
    )

    if len(recent_stats) < recent_window:
        return 0.0

    recent_avg = sum(r[0] for r in recent_stats) / len(recent_stats)

    # Difference as fraction of season average
    toi_diff_pct = (recent_avg - season_avg) / season_avg

    # Scale: +10% TOI increase = +0.15 upside
    return max(-0.2, min(0.2, toi_diff_pct * 1.5))


def _process_vs_results_score(
    session: Session,
    nhl_id: int,
    season: str,
    situation: str,
    min_gp: int = 15,
) -> float:
    """Compare on-ice process (xGF%) to actual results (goals).

    High xGF% with low actual scoring → unlucky, positive upside.
    Low xGF% with high actual scoring → lucky, negative upside.

    Uses GameOnIceStats for xGF% and GameIndividualStats for goals.
    """
    # Get on-ice xGF%
    oi_stats = (
        session.query(
            func.count().label("gp"),
            func.avg(GameOnIceStats.xgf_pct).label("avg_xgf_pct"),
        )
        .filter(
            GameOnIceStats.nhl_id == nhl_id,
            GameOnIceStats.season == season,
            GameOnIceStats.situation == situation,
        )
        .first()
    )

    if not oi_stats or oi_stats.gp < min_gp or oi_stats.avg_xgf_pct is None:
        return 0.0

    # Get actual goal production (per-60)
    ind_stats = (
        session.query(
            func.avg(GameIndividualStats.goals_per_60).label("avg_goals"),
            func.avg(GameIndividualStats.total_assists_per_60).label("avg_assists"),
            func.avg(GameIndividualStats.toi).label("avg_toi"),
        )
        .filter(
            GameIndividualStats.nhl_id == nhl_id,
            GameIndividualStats.season == season,
            GameIndividualStats.situation == situation,
        )
        .first()
    )

    if (
        not ind_stats
        or ind_stats.avg_goals is None
        or ind_stats.avg_assists is None
        or ind_stats.avg_toi is None
        or ind_stats.avg_toi <= 0
    ):
        return 0.0

    # xGF% indicates how much of the expected goal share goes the player's way
    # League average xGF% is ~50%. Above 55% = strong process.
    # If process is strong but points aren't coming, that's upside.
    xgf_quality = (oi_stats.avg_xgf_pct - 50.0) / 100.0  # -0.5 to 0.5, centered at 0

    # Points per game (proxy for "results")
    toi_frac = ind_stats.avg_toi / 60.0
    pts_per_game = (ind_stats.avg_goals + ind_stats.avg_assists) * toi_frac

    # If xGF% is high but points are low relative to expected,
    # there's a gap between process and results
    # Use a simple heuristic: xGF quality signal, tempered by whether
    # the player is actually underproducing
    if xgf_quality > 0 and pts_per_game < 0.5:
        # Strong process, low results — upside
        return min(0.2, xgf_quality * 0.5)
    elif xgf_quality < -0.05 and pts_per_game > 0.8:
        # Weak process, high results — regression risk
        return max(-0.2, xgf_quality * 0.5)

    return max(-0.15, min(0.15, xgf_quality * 0.3))

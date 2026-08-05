"""Opportunity model — quantify how favorable a player's current situation is.

Returns a score in [-1.0, 1.0]:
    Positive = situation is improving (more ice time, better deployment)
    Negative = situation is deteriorating
    ~0 = stable situation

Opportunity is distinct from upside. Upside measures individual talent
ceiling ("how good CAN this player be?"). Opportunity measures current
situational favorability ("how good is their environment RIGHT NOW?").

Opportunity signals are inherently temporary — an injury-driven promotion
ends when the teammate returns. Evaluated on short horizons (days to weeks).

See docs/upside-and-opportunity.md for the full framework.

Sources everything from `GameAdvancedStats` (joined to `Game` for the
date filter). Every query respects `as_of: Optional[date]` with a
strict-less-than cutoff (`Game.date < as_of`) so backtests don't leak.
"""

import logging
from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.models import Game, GameAdvancedStats
from src.analytics.rapm.metrics import opportunity_features

logger = logging.getLogger(__name__)

SEASON_START_DEFAULT = date(2025, 10, 1)

_RECENT_WINDOW = 10
_MIN_GP = 15


def compute_opportunity_score(
    session: Session,
    nhl_id: int,
    as_of: Optional[date] = None,
    season_start: date = SEASON_START_DEFAULT,
) -> float:
    """Estimate a player's situational opportunity in [-1.0, 1.0].

    Components:
    1. TOI trend: recent ice time vs season average (coach giving more minutes)
    2. Deployment share: growing share of team's 5v5 and PP ice time
    3. Linemate quality: RAPM-derived teammate quality trends and elevator effects
    """
    toi_trend = _toi_trend_score(session, nhl_id, as_of, season_start)
    deployment = _deployment_share_score(session, nhl_id, as_of, season_start)
    linemate = _linemate_opportunity_score(session, nhl_id, as_of)

    total = toi_trend + deployment + linemate
    return max(-1.0, min(1.0, round(total, 3)))


def compute_opportunity_breakdown(
    session: Session,
    nhl_id: int,
    as_of: Optional[date] = None,
    season_start: date = SEASON_START_DEFAULT,
) -> dict[str, float]:
    """Return individual opportunity components for debugging/display."""
    return {
        "toi_trend": _toi_trend_score(session, nhl_id, as_of, season_start),
        "deployment_share": _deployment_share_score(
            session, nhl_id, as_of, season_start
        ),
        "linemate_quality": _linemate_opportunity_score(session, nhl_id, as_of),
    }


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


def _toi_trend_score(
    session: Session,
    nhl_id: int,
    as_of: Optional[date],
    season_start: date,
) -> float:
    """Recent TOI vs season avg (all situations).

    Rising TOI = more opportunity to contribute (positive).
    """
    q = (
        session.query(Game.date, GameAdvancedStats.toi_seconds)
        .filter(
            GameAdvancedStats.player_id == nhl_id,
            GameAdvancedStats.situation == "all",
            GameAdvancedStats.toi_seconds.isnot(None),
        )
    )
    rows = _apply_cutoff(q, as_of, season_start).order_by(Game.date.desc()).all()

    if len(rows) < _MIN_GP or len(rows) < _RECENT_WINDOW:
        return 0.0

    toi_values = [float(r.toi_seconds) for r in rows]
    season_avg = sum(toi_values) / len(toi_values)
    if season_avg <= 0:
        return 0.0

    recent_avg = sum(toi_values[:_RECENT_WINDOW]) / _RECENT_WINDOW
    toi_diff_pct = (recent_avg - season_avg) / season_avg

    return max(-0.2, min(0.2, toi_diff_pct * 1.5))


def _deployment_share_score(
    session: Session,
    nhl_id: int,
    as_of: Optional[date],
    season_start: date,
) -> float:
    """Recent vs season 5v5 and PP deployment share.

    Growing share of team ice time = coach trust rising.
    """
    score = 0.0
    score += _share_trend("5v5", session, nhl_id, as_of, season_start) * 0.15
    score += _share_trend("pp", session, nhl_id, as_of, season_start) * 0.10
    return max(-0.25, min(0.25, score))


def _share_trend(
    situation: str,
    session: Session,
    nhl_id: int,
    as_of: Optional[date],
    season_start: date,
) -> float:
    """Return (recent_share - season_share) / season_share for one situation."""
    player_q = session.query(
        Game.date.label("date"),
        GameAdvancedStats.game_id.label("game_id"),
        GameAdvancedStats.team_id.label("team_id"),
        GameAdvancedStats.toi_seconds.label("player_toi"),
    ).filter(
        GameAdvancedStats.player_id == nhl_id,
        GameAdvancedStats.situation == situation,
        GameAdvancedStats.toi_seconds.isnot(None),
    )
    player_rows = _apply_cutoff(player_q, as_of, season_start).order_by(
        Game.date.desc()
    ).all()

    if len(player_rows) < _MIN_GP or len(player_rows) < _RECENT_WINDOW:
        return 0.0

    game_team_pairs = [(r.game_id, r.team_id) for r in player_rows]
    team_totals_q = session.query(
        GameAdvancedStats.game_id,
        GameAdvancedStats.team_id,
        func.sum(GameAdvancedStats.toi_seconds).label("team_toi"),
    ).filter(
        GameAdvancedStats.situation == situation,
    ).group_by(
        GameAdvancedStats.game_id, GameAdvancedStats.team_id,
    )
    team_totals_q = team_totals_q.filter(
        GameAdvancedStats.game_id.in_([gid for gid, _ in game_team_pairs]),
    )
    team_totals = {
        (row.game_id, row.team_id): float(row.team_toi or 0)
        for row in team_totals_q.all()
    }

    shares: list[float] = []
    for row in player_rows:
        team_toi = team_totals.get((row.game_id, row.team_id), 0.0)
        if team_toi <= 0:
            continue
        shares.append(float(row.player_toi) / team_toi)

    if len(shares) < _MIN_GP or len(shares) < _RECENT_WINDOW:
        return 0.0

    season_share = sum(shares) / len(shares)
    if season_share <= 0:
        return 0.0

    recent_share = sum(shares[:_RECENT_WINDOW]) / _RECENT_WINDOW
    delta_pct = (recent_share - season_share) / season_share
    return max(-1.0, min(1.0, delta_pct))


def _linemate_opportunity_score(
    session: Session,
    nhl_id: int,
    as_of: Optional[date],
) -> float:
    """RAPM-derived linemate quality signal.

    Three sub-signals:
    - linemate_quality_delta: recent (5g) vs longer-term (20g) teammate
      quality. Positive = coach recently promoted this player to better
      linemates — strong short-term opportunity signal.
    - deployment_gap: linemate quality minus own RAPM rating. Positive =
      playing with teammates above your own level (coach trusts you in a
      role beyond your baseline).
    - elevator_nearby: max elevation score among recent linemates. Playing
      alongside a known teammate-booster amplifies short-term production.

    Returns 0.0 if RAPM data is unavailable (model not yet trained).
    """
    try:
        feats = opportunity_features(session, nhl_id, as_of=as_of)
    except Exception:
        logger.debug("RAPM features unavailable for player %d", nhl_id)
        return 0.0

    score = 0.0

    if feats.linemate_quality_delta is not None:
        score += max(-0.10, min(0.10, feats.linemate_quality_delta * 0.5))

    if feats.deployment_gap is not None:
        score += max(-0.10, min(0.10, feats.deployment_gap * 0.3))

    if feats.elevator_nearby is not None and feats.elevator_nearby > 0:
        score += min(0.10, feats.elevator_nearby * 0.2)

    return max(-0.25, min(0.25, score))

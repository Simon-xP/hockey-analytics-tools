"""Upside model — quantify how much better a player could get.

Returns a score in [-1.0, 1.0]:
    Positive = underperforming their underlying talent (buy candidate)
    Negative = overperforming their talent (sell/regression risk)
    ~0 = performing as expected

Sources everything from `GameAdvancedStats` (joined to `Game` for the
date filter). No dependency on the NST tables, which are only populated
for 2023-24 / 2024-25 and would return zero for a 2025-26 backtest.

Every query respects `as_of: Optional[date]` with a strict-less-than
cutoff (`Game.date < as_of`) so backtests don't leak future stats.
"""

from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.models import Game, GameAdvancedStats


SEASON_START_DEFAULT = date(2025, 10, 1)

_RECENT_WINDOW = 10
_MIN_GP = 15


def compute_upside_score(
    session: Session,
    nhl_id: int,
    as_of: Optional[date] = None,
    season_start: date = SEASON_START_DEFAULT,
) -> float:
    """Estimate a player's upside in [-1.0, 1.0].

    Components (each clamped to ~[-0.3, 0.3] and summed):
    1. Shooting luck: current goals-over-expected vs player's own EB-shrunk career baseline
    2. Process vs results: on-ice xGF% vs actual scoring
    3. Deployment share: 5v5 and PP TOI share trends
    """
    shooting = _shooting_luck_score(session, nhl_id, as_of, season_start)
    toi_trend = _toi_trend_score(session, nhl_id, as_of, season_start)
    process = _process_vs_results_score(session, nhl_id, as_of, season_start)
    deployment = _deployment_share_score(session, nhl_id, as_of, season_start)

    total = shooting + toi_trend + process + deployment
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
        "toi_trend": _toi_trend_score(session, nhl_id, as_of, season_start),
        "process_vs_results": _process_vs_results_score(
            session, nhl_id, as_of, season_start
        ),
        "deployment_share": _deployment_share_score(
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
    ingestion (pre-2021 seasons). See memory: project_upside_vision.md.
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


def _toi_trend_score(
    session: Session,
    nhl_id: int,
    as_of: Optional[date],
    season_start: date,
) -> float:
    """Recent TOI vs season avg (all situations).

    Rising TOI = more opportunity to contribute (positive upside).
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

    # +10% TOI → +0.15 upside
    return max(-0.2, min(0.2, toi_diff_pct * 1.5))


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

    xgf_pct = xgf / (xgf + xga) * 100.0  # percentage, league avg ~50
    xgf_quality = (xgf_pct - 50.0) / 100.0  # centered, range roughly [-0.3, 0.3]

    pts = float((stats.total_goals or 0) + (stats.total_assists or 0))
    pts_per_game = pts / stats.gp

    if xgf_quality > 0 and pts_per_game < 0.5:
        return min(0.2, xgf_quality * 0.5)
    if xgf_quality < -0.05 and pts_per_game > 0.8:
        return max(-0.2, xgf_quality * 0.5)
    return max(-0.15, min(0.15, xgf_quality * 0.3))


def _deployment_share_score(
    session: Session,
    nhl_id: int,
    as_of: Optional[date],
    season_start: date,
) -> float:
    """Recent vs season 5v5 and PP deployment share.

    Growing share of team ice time = coach trust rising (upside).
    Computed as `player_toi / team_total_toi` per game, so game length
    and OT don't bias the trend.
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
    """Return (recent_share − season_share) / season_share for one situation.

    Clamped loosely to [-1, 1]. Caller scales it into an upside contribution.
    """
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

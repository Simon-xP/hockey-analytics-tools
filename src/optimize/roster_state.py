"""Daily roster state — the foundation for transaction decisions.

Computes a RosterPlayerState for every player on the roster, combining:
- Forecast FPTS/GP from trailing stats (v2 model when available)
- Remaining schedule this week
- Injury status from player_injuries table (Daily Faceoff + LLM parsed)
- Upside score from the upside model
- Opportunity score (placeholder until model is built)

All queries respect as_of for backtest safety.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from src.core.models import Player
from src.core.queries.schedule import ScheduleProvider
from src.core.queries.stats import StatsProvider
from src.optimize.injuries import estimate_games_missed, load_injuries
from src.predict.signals.upside import compute_upside_score


@dataclass
class RosterPlayerState:
    nhl_id: int
    name: str
    positions: list[str]
    team_id: int
    team_abbrev: str

    # Core value
    fpts_per_gp: float
    remaining_games_this_week: int
    remaining_weekly_fpts: float

    # Injury status (from player_injuries table / Yahoo)
    injury_status: str | None      # "IR+", "IR", "OUT", "DTD", or None
    estimated_games_missed: int    # estimated games this player will miss
    soonest_return: date | None
    latest_return: date | None

    # Adjustments
    upside_score: float            # [-1, 1] from upside model
    opportunity_score: float       # [-1, 1] placeholder


def compute_roster_state(
    session: Session,
    roster_nhl_ids: set[int],
    as_of: date,
    week_end: date | None = None,
) -> list[RosterPlayerState]:
    """Build RosterPlayerState for every player on the roster.

    Args:
        session: DB session.
        roster_nhl_ids: NHL IDs currently on the roster.
        as_of: Decision date — stats use Game.date < as_of.
        week_end: Sunday of the current fantasy week. If None, computed
                  from as_of (next Sunday).
    """
    if not roster_nhl_ids:
        return []

    if week_end is None:
        days_until_sunday = 6 - as_of.weekday()
        week_end = as_of + timedelta(days=days_until_sunday)

    stats = StatsProvider(session=session, as_of=as_of)
    schedule = ScheduleProvider(session=session, as_of=as_of)
    injuries = load_injuries(session, roster_nhl_ids, as_of)

    players = (
        session.query(Player)
        .filter(Player.nhl_id.in_(roster_nhl_ids))
        .all()
    )

    results = []
    for player in players:
        nhl_id = player.nhl_id
        team_id = player.team_id or 0
        team_abbrev = player.team.abbrev if player.team else ""

        positions = _parse_positions(player)

        fpts_data = stats.get_player_fpts_per_gp(
            nhl_id, lookback_days=30, min_gp=1,
        )
        if not fpts_data:
            fpts_data = stats.get_player_fpts_per_gp(
                nhl_id, min_gp=1,
            )
        fpts_per_gp = fpts_data["fpts_per_gp"] if fpts_data else 0.0

        remaining_games = 0
        if team_id:
            games = schedule.get_team_games_in_range(
                team_id, as_of, week_end,
            )
            remaining_games = len(games)

        injury_info = injuries.get(nhl_id)
        injury_status = None
        estimated_missed = 0
        soonest_return = None
        latest_return = None
        if injury_info:
            injury_status = injury_info["injury_status"]
            soonest_return = injury_info["soonest_return"]
            latest_return = injury_info["latest_return"]
            estimated_missed = estimate_games_missed(
                session, team_id, as_of, week_end,
                soonest_return, latest_return,
            )

        upside = compute_upside_score(session, nhl_id, as_of=as_of)

        remaining_weekly_fpts = fpts_per_gp * max(0, remaining_games - estimated_missed)

        results.append(RosterPlayerState(
            nhl_id=nhl_id,
            name=player.full_name,
            positions=positions,
            team_id=team_id,
            team_abbrev=team_abbrev,
            fpts_per_gp=round(fpts_per_gp, 4),
            remaining_games_this_week=remaining_games,
            remaining_weekly_fpts=round(remaining_weekly_fpts, 2),
            injury_status=injury_status,
            estimated_games_missed=estimated_missed,
            soonest_return=soonest_return,
            latest_return=latest_return,
            upside_score=upside,
            opportunity_score=0.0,
        ))

    return results


def _parse_positions(player: Player) -> list[str]:
    if player.yahoo_positions:
        return [p.strip() for p in player.yahoo_positions.split(",") if p.strip()]
    return [player.position or "F"]

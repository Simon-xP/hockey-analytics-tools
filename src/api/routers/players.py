"""Player API — forecasts, trends, search."""

from datetime import date

from fastapi import APIRouter, Query

from src.core.db import get_session
from src.core.models import Player, GameIndividualStats

router = APIRouter()


@router.get("/search")
def search_players(q: str = Query(..., min_length=2)):
    """Search players by name."""
    with get_session() as session:
        players = (
            session.query(Player)
            .filter(Player.full_name.ilike(f"%{q}%"))
            .limit(20)
            .all()
        )

        return {
            "results": [
                {
                    "nhl_id": p.nhl_id,
                    "name": p.full_name,
                    "position": p.position,
                    "team_id": p.team_id,
                }
                for p in players
            ]
        }


@router.get("/{nhl_id}")
def get_player(nhl_id: int):
    """Get player info with recent stats."""
    with get_session() as session:
        player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
        if not player:
            return {"error": "Player not found"}

        # Recent game logs (last 10)
        recent = (
            session.query(GameIndividualStats)
            .filter(
                GameIndividualStats.nhl_id == nhl_id,
                GameIndividualStats.situation == "all",
            )
            .order_by(GameIndividualStats.game_date.desc())
            .limit(10)
            .all()
        )

        game_logs = []
        for gs in recent:
            game_logs.append({
                "date": str(gs.game_date),
                "opponent": gs.opponent_abbrev,
                "is_home": gs.is_home,
                "toi": gs.toi,
                "goals_per_60": gs.goals_per_60,
                "assists_per_60": gs.total_assists_per_60,
                "shots_per_60": gs.shots_per_60,
                "ixg_per_60": gs.ixg_per_60,
                "hits_per_60": gs.hits_per_60,
                "blocked_per_60": gs.shots_blocked_per_60,
                "sh_pct": gs.sh_pct,
                "ipp": gs.ipp,
            })

        return {
            "player": {
                "nhl_id": player.nhl_id,
                "name": player.full_name,
                "position": player.position,
                "team_id": player.team_id,
            },
            "recent_games": game_logs,
        }


@router.get("/{nhl_id}/forecast")
def get_forecast(nhl_id: int, game_date: str | None = None):
    """Get forecast for a player."""
    from src.tools.forecasting import forecast

    target_date = date.fromisoformat(game_date) if game_date else date.today()

    try:
        predictions = forecast(nhl_id=nhl_id, game_date=target_date)
        return {
            "nhl_id": nhl_id,
            "game_date": str(target_date),
            "predictions": predictions,
        }
    except Exception as e:
        return {"error": str(e)}

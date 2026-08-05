"""Player API — forecasts, trends, search."""

from datetime import date

import httpx
from fastapi import APIRouter, HTTPException, Query

from src.api.schemas import (
    PlayerSearchResult,
    PlayerDetailResponse,
    ForecastResponse,
)

from src.core.db import get_session
from src.core.models import Player, Team, GameIndividualStats, GameAdvancedStats, Game

router = APIRouter()


@router.get("/search", response_model=PlayerSearchResult)
def search_players(q: str = Query(..., min_length=2)):
    """Search players by name."""
    with get_session() as session:
        players = (
            session.query(Player)
            .filter(Player.full_name.ilike(f"%{q}%"))
            .limit(20)
            .all()
        )

        teams = {t.team_id: t.abbrev for t in session.query(Team).all()}

        return {
            "results": [
                {
                    "nhl_id": p.nhl_id,
                    "name": p.full_name,
                    "position": p.yahoo_positions or p.position,
                    "team": teams.get(p.team_id),
                }
                for p in players
            ]
        }


@router.get("/{nhl_id}", response_model=PlayerDetailResponse)
async def get_player(nhl_id: int):
    """Get player info with recent stats."""
    with get_session() as session:
        player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")

        # Recent game logs (last 10) from advanced stats
        from sqlalchemy import text
        recent_rows = session.execute(
            text("""
                SELECT g.date, gas.toi_seconds, gas.goals, gas.assists,
                       gas.shots, gas.hits, gas.blocks, gas.ixg,
                       gas.cf, gas.ca, gas.gf, gas.ga, gas.ipp,
                       gas.opponent_team_id,
                       CASE WHEN g.home_team_id = gas.team_id THEN true ELSE false END as is_home
                FROM game_advanced_stats gas
                JOIN games g ON gas.game_id = g.game_id
                WHERE gas.player_id = :pid AND gas.situation = 'all'
                      AND gas.toi_seconds > 0
                ORDER BY g.date DESC LIMIT 10
            """),
            {"pid": nhl_id},
        ).fetchall()

        teams_map = {t.team_id: t.abbrev for t in session.query(Team).all()}

        game_logs = []
        for r in recent_rows:
            toi_min = r[1] / 60 if r[1] else 0
            game_logs.append({
                "date": str(r[0]),
                "opponent": teams_map.get(r[13]),
                "is_home": r[14],
                "toi": round(toi_min, 1),
                "goals": r[2],
                "assists": r[3],
                "shots": r[4],
                "hits": r[5],
                "blocks": r[6],
                "ixg": round(r[7], 2) if r[7] else 0,
                "cf": r[8],
                "ca": r[9],
                "gf": r[10],
                "ga": r[11],
                "ipp": round(r[12], 2) if r[12] else None,
            })

        team = session.query(Team).filter(Team.team_id == player.team_id).first()
        is_goalie = player.position == "G"

        result = {
            "player": {
                "nhl_id": player.nhl_id,
                "name": player.full_name,
                "position": player.yahoo_positions or player.position,
                "team": team.abbrev if team else None,
                "is_goalie": is_goalie,
            },
            "recent_games": game_logs,
        }

        # Fetch goalie stats from NHL API
        if is_goalie:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"https://api-web.nhle.com/v1/player/{nhl_id}/landing",
                        timeout=5,
                    )
                if resp.is_success:
                    nhl_data = resp.json()
                    season_stats = (
                        nhl_data.get("featuredStats", {})
                        .get("regularSeason", {})
                        .get("subSeason", {})
                    )
                    career_stats = (
                        nhl_data.get("featuredStats", {})
                        .get("regularSeason", {})
                        .get("career", {})
                    )
                    result["goalie_stats"] = {
                        "season": {
                            "gp": season_stats.get("gamesPlayed"),
                            "wins": season_stats.get("wins"),
                            "losses": season_stats.get("losses"),
                            "otl": season_stats.get("otLosses"),
                            "gaa": round(season_stats.get("goalsAgainstAvg", 0), 2),
                            "sv_pct": round(season_stats.get("savePctg", 0), 3),
                            "shutouts": season_stats.get("shutouts"),
                        },
                        "career": {
                            "gp": career_stats.get("gamesPlayed"),
                            "wins": career_stats.get("wins"),
                            "losses": career_stats.get("losses"),
                            "gaa": round(career_stats.get("goalsAgainstAvg", 0), 2),
                            "sv_pct": round(career_stats.get("savePctg", 0), 3),
                            "shutouts": career_stats.get("shutouts"),
                        },
                    }
                    # Headshot
                    result["player"]["headshot"] = nhl_data.get("headshot")
            except Exception:
                pass

        return result


@router.get("/{nhl_id}/forecast", response_model=ForecastResponse)
def get_forecast(nhl_id: int, game_date: str | None = None):
    """Get per-game stat projections using v2 situation-split model.

    Returns projected per-game counts and fantasy points, broken down
    by situation (5v5, PP, PK, other) and combined total.
    """
    from pathlib import Path
    from src.predict.forecasting.model import SituationModel
    from src.predict.forecasting.toi_model import TOIPredictor
    from src.predict.forecasting.empirical_bayes import EmpiricalBayesPredictor
    from src.predict.forecasting.features import (
        extract_all_features, load_player_game_stats, extract_rolling_features,
    )
    from src.predict.forecasting.projections import project_per_game
    from src.predict.forecasting.constants import SITUATION_CONFIGS

    target_date = date.fromisoformat(game_date) if game_date else date.today()

    try:
        with get_session() as session:
            player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
            if not player:
                raise HTTPException(status_code=404, detail="Player not found")

            position = player.position or "C"
            team_id = player.team_id

            # Find the next game for this player's team
            from src.core.models import Game
            game = (
                session.query(Game)
                .filter(
                    Game.date >= target_date,
                    (Game.home_team_id == team_id) | (Game.away_team_id == team_id),
                )
                .order_by(Game.date)
                .first()
            )

            if not game:
                raise HTTPException(status_code=404, detail="No upcoming game found")

            home_team_id = game.home_team_id
            opp_team_id = (
                game.away_team_id if team_id == game.home_team_id
                else game.home_team_id
            )

            # Determine current season
            current_year = target_date.year if target_date.month >= 9 else target_date.year - 1

            # Load models
            model_dir = Path("models/forecasting_v2")
            models = {}
            for sit in ["5v5", "pp", "pk"]:
                path = model_dir / f"{sit}_model.pkl"
                if path.exists():
                    models[sit] = SituationModel.load(path)

            toi_predictor = TOIPredictor()
            eb_pk = EmpiricalBayesPredictor("pk", ["goals", "assists"])

            predicted_rates = {}
            predicted_toi = {}

            for situation in SITUATION_CONFIGS:
                # Predict TOI
                toi = toi_predictor.predict(
                    session, nhl_id, situation, target_date, current_year,
                )
                predicted_toi[situation] = toi

                if situation == "other":
                    # League-average rates for Other situations (4v4/3v3/EN).
                    # Too rare and noisy for individual prediction.
                    predicted_rates[situation] = {
                        "goals_per60": 2.38,
                        "assists_per60": 3.06,
                    }

                elif situation == "pk":
                    # Empirical Bayes for PK goals/assists
                    eb_rates = eb_pk.predict(session, nhl_id, target_date)
                    rates = {
                        "goals_per60": eb_rates.get("goals_per60", 0),
                        "assists_per60": eb_rates.get("assists_per60", 0),
                    }
                    # Poisson model for PK physical stats
                    if "pk" in models:
                        features = extract_all_features(
                            session, nhl_id, "pk", target_date,
                            team_id, opp_team_id, home_team_id,
                            position, current_year,
                        )
                        poisson_rates = models["pk"].predict(features, toi_seconds=toi)
                        rates.update(poisson_rates)
                    predicted_rates[situation] = rates

                elif situation in models:
                    # 5v5 and PP: standard XGBoost
                    features = extract_all_features(
                        session, nhl_id, situation, target_date,
                        team_id, opp_team_id, home_team_id,
                        position, current_year,
                    )
                    predicted_rates[situation] = models[situation].predict(features)

            # Combine into per-game projection
            projected = project_per_game(predicted_rates, predicted_toi)

            # Build response with situation breakdown
            team = session.query(Team).filter(Team.team_id == team_id).first()
            opp_team = session.query(Team).filter(Team.team_id == opp_team_id).first()

            return {
                "nhl_id": nhl_id,
                "name": player.full_name,
                "game_date": str(game.date),
                "opponent": opp_team.abbrev if opp_team else None,
                "is_home": team_id == home_team_id,
                "projection": {
                    "goals": round(projected.get("goals", 0), 3),
                    "assists": round(projected.get("assists", 0), 3),
                    "shots": round(projected.get("shots", 0), 2),
                    "hits": round(projected.get("hits", 0), 2),
                    "blocks": round(projected.get("blocks", 0), 2),
                    "fpts": round(projected.get("fpts", 0), 2),
                },
                "situation_breakdown": {
                    sit: {
                        "toi_min": round(predicted_toi.get(sit, 0) / 60, 1),
                        "rates": {
                            k: round(v, 2)
                            for k, v in predicted_rates.get(sit, {}).items()
                            if k.endswith("_per60")
                        },
                    }
                    for sit in ["5v5", "pp", "pk", "other"]
                },
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

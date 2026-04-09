"""Dashboard API — powers the main landing page."""

from datetime import date, timedelta

from fastapi import APIRouter
from sqlalchemy import func

from src.core.db import get_session
from src.core.models import Game, Team, Player, GameIndividualStats
from src.tools.fantasy.scoring import SKATER_WEIGHTS

router = APIRouter()


@router.get("/today")
def today_games():
    """Get today's games with team info."""
    today = date.today()

    with get_session() as session:
        games = (
            session.query(Game)
            .filter(Game.date == today)
            .all()
        )

        result = []
        for g in games:
            home = session.query(Team).filter(Team.team_id == g.home_team_id).first()
            away = session.query(Team).filter(Team.team_id == g.away_team_id).first()
            result.append({
                "game_id": g.game_id,
                "date": str(g.date),
                "home_team": {"id": home.team_id, "abbrev": home.abbrev, "name": home.full_name} if home else None,
                "away_team": {"id": away.team_id, "abbrev": away.abbrev, "name": away.full_name} if away else None,
                "home_score": g.home_score,
                "away_score": g.away_score,
            })

    return {"date": str(today), "games": result}


@router.get("/standings")
def standings():
    """Get current standings derived from game results."""
    with get_session() as session:
        teams = session.query(Team).all()
        team_map = {t.team_id: t for t in teams}

        # Get all completed games
        games = (
            session.query(Game)
            .filter(Game.home_score.isnot(None))
            .all()
        )

        # Compute W-L-OTL for each team
        records = {}
        for t in teams:
            records[t.team_id] = {
                "team_id": t.team_id,
                "abbrev": t.abbrev,
                "name": t.full_name,
                "wins": 0,
                "losses": 0,
                "gp": 0,
                "gf": 0,
                "ga": 0,
            }

        for g in games:
            h = records.get(g.home_team_id)
            a = records.get(g.away_team_id)
            if not h or not a:
                continue

            h["gp"] += 1
            a["gp"] += 1
            h["gf"] += g.home_score
            h["ga"] += g.away_score
            a["gf"] += g.away_score
            a["ga"] += g.home_score

            if g.home_score > g.away_score:
                h["wins"] += 1
                a["losses"] += 1
            else:
                a["wins"] += 1
                h["losses"] += 1

        result = sorted(records.values(), key=lambda x: x["wins"], reverse=True)

    return {"standings": result}


@router.get("/schedule-outlook")
def schedule_outlook(days: int = 7):
    """Teams with the most games in the next N days."""
    today = date.today()
    end = today + timedelta(days=days)

    with get_session() as session:
        games = (
            session.query(Game)
            .filter(Game.date >= today, Game.date <= end)
            .all()
        )

        teams = {t.team_id: t.abbrev for t in session.query(Team).all()}
        game_counts = {}

        for g in games:
            for tid in [g.home_team_id, g.away_team_id]:
                abbrev = teams.get(tid, "???")
                if abbrev not in game_counts:
                    game_counts[abbrev] = 0
                game_counts[abbrev] += 1

        result = sorted(
            [{"team": k, "games": v} for k, v in game_counts.items()],
            key=lambda x: x["games"],
            reverse=True,
        )

    return {"days": days, "start": str(today), "end": str(end), "teams": result}


@router.get("/regression")
def regression_candidates(season: str = "20242025", min_gp: int = 20, limit: int = 10):
    """Find buy-low and sell-high candidates based on shooting luck.

    Buy low: players with low SH% relative to their ixG rate (unlucky).
    Sell high: players with high SH% relative to their ixG rate (lucky).
    """
    with get_session() as session:
        # Get players with enough games
        player_stats = (
            session.query(
                GameIndividualStats.nhl_id,
                func.count().label("gp"),
                func.avg(GameIndividualStats.goals_per_60).label("avg_goals"),
                func.avg(GameIndividualStats.ixg_per_60).label("avg_ixg"),
                func.avg(GameIndividualStats.sh_pct).label("avg_sh_pct"),
                func.avg(GameIndividualStats.ipp).label("avg_ipp"),
                func.avg(GameIndividualStats.shots_per_60).label("avg_shots"),
                func.avg(GameIndividualStats.toi).label("avg_toi"),
            )
            .filter(
                GameIndividualStats.season == season,
                GameIndividualStats.situation == "all",
            )
            .group_by(GameIndividualStats.nhl_id)
            .having(func.count() >= min_gp)
            .all()
        )

        candidates = []
        for ps in player_stats:
            if ps.avg_ixg is None or ps.avg_goals is None or ps.avg_ixg == 0:
                continue

            # Goals above/below expected: positive = overperforming
            goal_diff = ps.avg_goals - ps.avg_ixg

            player = session.query(Player).filter(Player.nhl_id == ps.nhl_id).first()
            if not player or player.position == "G":
                continue

            team = session.query(Team).filter(Team.team_id == player.team_id).first()

            candidates.append({
                "nhl_id": ps.nhl_id,
                "name": player.full_name,
                "position": player.position,
                "team": team.abbrev if team else None,
                "gp": ps.gp,
                "goals_per_60": round(ps.avg_goals, 2),
                "ixg_per_60": round(ps.avg_ixg, 2),
                "goal_diff": round(goal_diff, 2),
                "sh_pct": round(ps.avg_sh_pct, 1) if ps.avg_sh_pct else None,
                "avg_toi": round(ps.avg_toi, 1) if ps.avg_toi else None,
            })

        # Sort by goal_diff
        buy_low = sorted(candidates, key=lambda x: x["goal_diff"])[:limit]
        sell_high = sorted(candidates, key=lambda x: x["goal_diff"], reverse=True)[:limit]

    return {"buy_low": buy_low, "sell_high": sell_high}


@router.get("/optimal-adds")
def optimal_adds(season: str = "20242025", min_gp: int = 20, limit: int = 50):
    """Rank all skaters by projected fantasy points per game.

    Computes FPTS/GP from actual season per-60 rates and TOI,
    using the league's scoring weights. Higher = more valuable.
    """
    with get_session() as session:
        player_stats = (
            session.query(
                GameIndividualStats.nhl_id,
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
                GameIndividualStats.season == season,
                GameIndividualStats.situation == "all",
            )
            .group_by(GameIndividualStats.nhl_id)
            .having(func.count() >= min_gp)
            .all()
        )

        results = []
        for ps in player_stats:
            if not ps.avg_toi or ps.avg_toi <= 0:
                continue

            player = session.query(Player).filter(Player.nhl_id == ps.nhl_id).first()
            if not player or player.position == "G":
                continue

            team = session.query(Team).filter(Team.team_id == player.team_id).first()

            toi_frac = ps.avg_toi / 60.0
            fpts = 0.0
            stat_breakdown = {}

            for col, cat, weight in [
                (ps.avg_goals, "goals", SKATER_WEIGHTS["goals"]),
                (ps.avg_assists, "assists", SKATER_WEIGHTS["assists"]),
                (ps.avg_shots, "shots", SKATER_WEIGHTS["shots"]),
                (ps.avg_hits, "hits", SKATER_WEIGHTS["hits"]),
                (ps.avg_blocks, "blocks", SKATER_WEIGHTS["blocks"]),
                (ps.avg_pim, "pim", SKATER_WEIGHTS["pim"]),
            ]:
                if col is not None:
                    per_game = col * toi_frac
                    pts = per_game * weight
                    fpts += pts
                    stat_breakdown[cat] = round(per_game, 2)

            results.append({
                "nhl_id": ps.nhl_id,
                "name": player.full_name,
                "position": player.position,
                "team": team.abbrev if team else None,
                "gp": ps.gp,
                "avg_toi": round(ps.avg_toi, 1),
                "fpts_per_gp": round(fpts, 2),
                "stats_per_gp": stat_breakdown,
            })

        results.sort(key=lambda x: x["fpts_per_gp"], reverse=True)

    return {"players": results[:limit], "scoring": SKATER_WEIGHTS}

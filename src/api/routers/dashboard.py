"""Dashboard API — powers the main landing page."""

from datetime import date, timedelta

from fastapi import APIRouter
from sqlalchemy import func

from src.api.schemas import (
    TodayGamesResponse,
    StandingsResponse,
    ScheduleOutlookResponse,
    RegressionResponse,
    OptimalAddsResponse,
)

from sqlalchemy import text as sa_text

from src.core.db import get_session
from src.core.models import Game, Team, Player, GameIndividualStats, GameAdvancedStats
from src.core.scoring import SKATER_WEIGHTS

router = APIRouter()


@router.get("/today", response_model=TodayGamesResponse)
def today_games():
    """Get today's games with team info, start time, and W-L records."""
    today = date.today()
    # NHL season starts in early October; treat Oct 1 as season boundary
    season_start = date(today.year, 10, 1) if today.month >= 10 else date(today.year - 1, 10, 1)

    with get_session() as session:
        games = (
            session.query(Game)
            .filter(Game.date == today)
            .all()
        )

        # Compute W-L-OTL records for every team from completed games this season
        completed = (
            session.query(Game)
            .filter(
                Game.home_score.isnot(None),
                Game.date < today,
                Game.date >= season_start,
            )
            .all()
        )
        records: dict[int, dict] = {}

        def _rec(tid: int) -> dict:
            if tid not in records:
                records[tid] = {"wins": 0, "losses": 0, "otl": 0}
            return records[tid]

        for cg in completed:
            if cg.home_score is None or cg.away_score is None:
                continue
            h = _rec(cg.home_team_id)
            a = _rec(cg.away_team_id)
            if cg.home_score > cg.away_score:
                h["wins"] += 1
                a["losses"] += 1
            else:
                a["wins"] += 1
                h["losses"] += 1

        def _fmt(tid: int) -> str:
            r = records.get(tid, {"wins": 0, "losses": 0, "otl": 0})
            return f"{r['wins']}-{r['losses']}-{r['otl']}"

        result = []
        for g in games:
            home = session.query(Team).filter(Team.team_id == g.home_team_id).first()
            away = session.query(Team).filter(Team.team_id == g.away_team_id).first()
            result.append({
                "game_id": g.game_id,
                "date": str(g.date),
                "start_time_utc": g.start_time_utc.isoformat() if g.start_time_utc else None,
                "home_team": {
                    "id": home.team_id,
                    "abbrev": home.abbrev,
                    "name": home.full_name,
                    "record": _fmt(home.team_id),
                } if home else None,
                "away_team": {
                    "id": away.team_id,
                    "abbrev": away.abbrev,
                    "name": away.full_name,
                    "record": _fmt(away.team_id),
                } if away else None,
                "home_score": g.home_score,
                "away_score": g.away_score,
            })

    return {"date": str(today), "games": result}


@router.get("/standings", response_model=StandingsResponse)
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


@router.get("/schedule-outlook", response_model=ScheduleOutlookResponse)
def schedule_outlook():
    """Teams with the most games this fantasy week, with per-day breakdown."""
    today = date.today()
    # Fantasy week = Monday to Sunday
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    week_dates = [monday + timedelta(days=i) for i in range(7)]
    day_labels = [d.strftime("%a") for d in week_dates]

    with get_session() as session:
        games = (
            session.query(Game)
            .filter(Game.date >= monday, Game.date <= sunday)
            .all()
        )

        teams_map = {t.team_id: t.abbrev for t in session.query(Team).all()}

        # Build team -> list of booleans for each day
        team_days: dict[str, list[bool]] = {}
        for g in games:
            for tid in [g.home_team_id, g.away_team_id]:
                abbrev = teams_map.get(tid, "???")
                if abbrev not in team_days:
                    team_days[abbrev] = [False] * 7
                day_idx = (g.date - monday).days
                if 0 <= day_idx < 7:
                    team_days[abbrev][day_idx] = True

        result = []
        for abbrev, days in team_days.items():
            result.append({
                "team": abbrev,
                "games": sum(days),
                "days": days,
            })

        result.sort(key=lambda x: x["games"], reverse=True)

    return {
        "week_start": str(monday),
        "week_end": str(sunday),
        "day_labels": day_labels,
        "teams": result,
    }


@router.get("/regression", response_model=RegressionResponse)
def regression_candidates(
    season: str = "20242025",
    min_gp: int = 30,
    limit: int = 10,
):
    """Find buy-low and sell-high candidates based on shooting luck.

    Uses GameAdvancedStats (NHL API data) to compare actual goals vs
    expected goals (ixG). Players outperforming their xG may regress
    (sell-high), players underperforming may bounce back (buy-low).

    Note: elite shooters sustainably beat xG — see
    docs/shooting-talent-and-xg-limitations.md for caveats.
    """
    with get_session() as session:
        # Aggregate 5v5 stats per player for the season
        start_year = int(season[:4])
        start_gid = start_year * 1_000_000
        end_gid = (start_year + 1) * 1_000_000

        rows = session.execute(sa_text("""
            SELECT gas.player_id,
                   COUNT(DISTINCT gas.game_id) as gp,
                   SUM(gas.goals) as goals,
                   SUM(gas.ixg) as ixg,
                   SUM(gas.shots) as shots,
                   SUM(gas.toi_seconds) as toi
            FROM game_advanced_stats gas
            WHERE gas.situation = '5v5'
                  AND gas.game_id >= :start AND gas.game_id < :end
                  AND gas.toi_seconds > 0
            GROUP BY gas.player_id
            HAVING COUNT(DISTINCT gas.game_id) >= :min_gp
        """), {"start": start_gid, "end": end_gid, "min_gp": min_gp}).fetchall()

        candidates = []
        for r in rows:
            player_id, gp, goals, ixg, shots, toi = r
            if not ixg or ixg == 0 or not toi or toi < 600 * gp:
                continue

            # Per-60 rates
            goals_per_60 = goals / toi * 3600
            ixg_per_60 = ixg / toi * 3600
            goal_diff = goals_per_60 - ixg_per_60
            sh_pct = goals / shots * 100 if shots > 0 else 0

            player = session.query(Player).filter(Player.nhl_id == player_id).first()
            if not player or player.position == "G":
                continue

            team = session.query(Team).filter(Team.team_id == player.team_id).first()

            candidates.append({
                "nhl_id": player_id,
                "name": player.full_name,
                "position": player.yahoo_positions or player.position,
                "team": team.abbrev if team else None,
                "gp": gp,
                "goals_per_60": round(goals_per_60, 2),
                "ixg_per_60": round(ixg_per_60, 2),
                "goal_diff": round(goal_diff, 2),
                "sh_pct": round(sh_pct, 1),
                "avg_toi": round(toi / gp / 60, 1),
            })

        buy_low = sorted(candidates, key=lambda x: x["goal_diff"])[:limit]
        sell_high = sorted(candidates, key=lambda x: x["goal_diff"], reverse=True)[:limit]

    return {"buy_low": buy_low, "sell_high": sell_high}


@router.get("/optimal-adds", response_model=OptimalAddsResponse)
def optimal_adds(season: str = "20252026", min_gp: int = 20, limit: int = 50):
    """Rank all skaters by projected fantasy points per game.

    Uses GameAdvancedStats across all situations to compute FPTS/GP
    with PP/SH bonuses. Higher = more valuable.
    """
    with get_session() as session:
        start_year = int(season[:4])
        start_gid = start_year * 1_000_000
        end_gid = (start_year + 1) * 1_000_000

        rows = session.execute(sa_text("""
            SELECT gas.player_id,
                   COUNT(DISTINCT gas.game_id) as gp,
                   SUM(CASE WHEN gas.situation = 'all' THEN gas.goals ELSE 0 END) as goals,
                   SUM(CASE WHEN gas.situation = 'all' THEN gas.assists ELSE 0 END) as assists,
                   SUM(CASE WHEN gas.situation = 'all' THEN gas.shots ELSE 0 END) as shots,
                   SUM(CASE WHEN gas.situation = 'all' THEN gas.hits ELSE 0 END) as hits,
                   SUM(CASE WHEN gas.situation = 'all' THEN gas.blocks ELSE 0 END) as blocks,
                   SUM(CASE WHEN gas.situation = 'all' THEN gas.toi_seconds ELSE 0 END) as toi,
                   SUM(CASE WHEN gas.situation = 'pp' THEN gas.goals ELSE 0 END) as pp_goals,
                   SUM(CASE WHEN gas.situation = 'pp' THEN gas.assists ELSE 0 END) as pp_assists,
                   SUM(CASE WHEN gas.situation = 'pk' THEN gas.goals ELSE 0 END) as sh_goals,
                   SUM(CASE WHEN gas.situation = 'pk' THEN gas.assists ELSE 0 END) as sh_assists,
                   SUM(CASE WHEN gas.situation = 'all' THEN gas.ixg ELSE 0 END) as ixg
            FROM game_advanced_stats gas
            WHERE gas.game_id >= :start AND gas.game_id < :end
                  AND gas.toi_seconds > 0
            GROUP BY gas.player_id
            HAVING COUNT(DISTINCT gas.game_id) >= :min_gp
        """), {"start": start_gid, "end": end_gid, "min_gp": min_gp}).fetchall()

        from src.predict.forecasting.projections import PP_GOAL_BONUS, PP_ASSIST_BONUS, SH_GOAL_BONUS, SH_ASSIST_BONUS

        results = []
        for r in rows:
            (pid, gp, goals, assists, shots, hits, blocks, toi,
             pp_g, pp_a, sh_g, sh_a, ixg) = r

            if not gp or gp == 0:
                continue

            player = session.query(Player).filter(Player.nhl_id == pid).first()
            if not player or player.position == "G":
                continue
            team = session.query(Team).filter(Team.team_id == player.team_id).first()

            # Per-game stats
            gpg = goals / gp
            apg = assists / gp
            spg = shots / gp
            hpg = hits / gp
            bpg = blocks / gp

            # Fantasy points with situation bonuses
            fpts = (
                gpg * SKATER_WEIGHTS["goals"]
                + apg * SKATER_WEIGHTS["assists"]
                + spg * SKATER_WEIGHTS["shots"]
                + hpg * SKATER_WEIGHTS["hits"]
                + bpg * SKATER_WEIGHTS["blocks"]
                + (pp_g / gp) * PP_GOAL_BONUS
                + (pp_a / gp) * PP_ASSIST_BONUS
                + (sh_g / gp) * SH_GOAL_BONUS
                + (sh_a / gp) * SH_ASSIST_BONUS
            )

            results.append({
                "nhl_id": pid,
                "name": player.full_name,
                "position": player.yahoo_positions or player.position,
                "team": team.abbrev if team else None,
                "gp": gp,
                "avg_toi": round(toi / gp / 60, 1),
                "fpts_per_gp": round(fpts, 2),
                "stats_per_gp": {
                    "goals": round(gpg, 2),
                    "assists": round(apg, 2),
                    "shots": round(spg, 2),
                    "hits": round(hpg, 2),
                    "blocks": round(bpg, 2),
                    "pp_goals": round(pp_g / gp, 3),
                    "pp_assists": round(pp_a / gp, 3),
                },
            })

        results.sort(key=lambda x: x["fpts_per_gp"], reverse=True)

    return {"players": results[:limit], "scoring": SKATER_WEIGHTS}

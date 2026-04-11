"""Goalie matchup analysis — ranks teams by how goalie-friendly they are to stream against."""

from fastapi import APIRouter
from sqlalchemy import func, case, and_

from src.core.db import get_session
from src.core.models import Game, Team
from src.tools.fantasy.scoring import GOALIE_WEIGHTS

router = APIRouter()


def _compute_team_goalie_scores(season_prefix: int = 2025) -> list[dict]:
    """Compute average opposing goalie fantasy points per game for each team.

    For each team, looks at all their games and computes what the opposing
    goalie would have earned in fantasy points. Teams that allow fewer shots
    and goals give opposing goalies worse fantasy nights.

    Higher score = better team to stream a goalie against (they're bad).
    Lower score = worse team to stream against (they're good/hard to beat).
    """
    game_id_min = season_prefix * 1000000 + 20000
    game_id_max = season_prefix * 1000000 + 30000

    with get_session() as session:
        teams = {t.team_id: t for t in session.query(Team).all()}

        games = (
            session.query(Game)
            .filter(
                Game.home_score.isnot(None),
                Game.game_id > game_id_min,
                Game.game_id < game_id_max,
            )
            .all()
        )

        # For each team, compute what opposing goalies earned
        team_stats = {}
        for t in teams.values():
            team_stats[t.team_id] = {
                "team_id": t.team_id,
                "abbrev": t.abbrev,
                "name": t.full_name,
                "games": 0,
                "total_goals_for": 0,  # goals this team scored (bad for opposing goalie)
                "total_goals_against": 0,  # goals this team allowed
                "total_goalie_fpts": 0.0,
                "wins_against": 0,  # times opposing goalie won
                "shutouts_against": 0,  # times this team got shut out
            }

        for g in games:
            # From home team's perspective: opposing goalie is away goalie
            # Away goalie faces home team's shots
            h = team_stats.get(g.home_team_id)
            a = team_stats.get(g.away_team_id)
            if not h or not a:
                continue

            # Home team stats (what they did TO the away goalie)
            h["games"] += 1
            h["total_goals_for"] += g.home_score  # home team scored this many
            h["total_goals_against"] += g.away_score

            # Opposing goalie (away) stats against home team:
            # We don't have shots data per game in our DB, so approximate
            # using goals only for now. The key insight is goals against.
            home_scored = g.home_score
            away_won = g.away_score > g.home_score

            # Away goalie's fantasy points for this game against home team:
            # We don't have saves (no shots data), so we estimate
            # A typical NHL game has ~30 shots, so saves ≈ 30 - goals_against
            est_shots = 30  # league average approximation
            est_saves = max(0, est_shots - home_scored)
            goalie_fpts = (
                est_saves * GOALIE_WEIGHTS["saves"]
                + home_scored * GOALIE_WEIGHTS["goals_against"]
                + (GOALIE_WEIGHTS["wins"] if away_won else 0)
                + (GOALIE_WEIGHTS["shutouts"] if home_scored == 0 else 0)
            )
            h["total_goalie_fpts"] += goalie_fpts
            if away_won:
                h["wins_against"] += 1
            if home_scored == 0:
                h["shutouts_against"] += 1

            # Away team stats (what they did TO the home goalie)
            a["games"] += 1
            a["total_goals_for"] += g.away_score
            a["total_goals_against"] += g.home_score

            away_scored = g.away_score
            home_won = g.home_score > g.away_score

            est_saves_h = max(0, est_shots - away_scored)
            goalie_fpts_h = (
                est_saves_h * GOALIE_WEIGHTS["saves"]
                + away_scored * GOALIE_WEIGHTS["goals_against"]
                + (GOALIE_WEIGHTS["wins"] if home_won else 0)
                + (GOALIE_WEIGHTS["shutouts"] if away_scored == 0 else 0)
            )
            a["total_goalie_fpts"] += goalie_fpts_h
            if home_won:
                a["wins_against"] += 1
            if away_scored == 0:
                a["shutouts_against"] += 1

        # Compute per-game averages
        results = []
        for ts in team_stats.values():
            if ts["games"] == 0:
                continue
            gp = ts["games"]
            results.append({
                "team_id": ts["team_id"],
                "abbrev": ts["abbrev"],
                "name": ts["name"],
                "gp": gp,
                "goals_per_game": round(ts["total_goals_for"] / gp, 2),
                "goals_against_per_game": round(ts["total_goals_against"] / gp, 2),
                "opp_goalie_fpts_avg": round(ts["total_goalie_fpts"] / gp, 2),
                "opp_goalie_win_pct": round(ts["wins_against"] / gp * 100, 1),
                "shutout_pct": round(ts["shutouts_against"] / gp * 100, 1),
            })

        # Sort by opp_goalie_fpts_avg descending (best to stream against first)
        results.sort(key=lambda x: x["opp_goalie_fpts_avg"], reverse=True)

    return results


@router.get("/rankings")
def goalie_matchup_rankings():
    """Get all teams ranked by how goalie-friendly they are to stream against.

    Higher opp_goalie_fpts_avg = better to stream a goalie against this team.
    """
    rankings = _compute_team_goalie_scores()
    return {
        "rankings": rankings,
        "scoring": GOALIE_WEIGHTS,
        "note": "opp_goalie_fpts_avg uses estimated 30 shots/game. Will improve with actual shot data.",
    }


@router.get("/team/{abbrev}")
def goalie_matchup_team(abbrev: str):
    """Get detailed goalie matchup data for a specific team."""
    rankings = _compute_team_goalie_scores()
    team = next((r for r in rankings if r["abbrev"].upper() == abbrev.upper()), None)
    if not team:
        return {"error": "Team not found"}

    team["rank"] = next(
        i + 1 for i, r in enumerate(rankings) if r["abbrev"] == team["abbrev"]
    )
    team["total_teams"] = len(rankings)

    return team

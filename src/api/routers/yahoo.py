"""Yahoo Fantasy API routes — auth flow and league data."""

from datetime import date, timedelta

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_

from src.core.db import get_session
from src.core.models import Player, Team, Game, GameIndividualStats
from src.core.resolver import resolve_player
from src.ingest.yahoo.auth import (
    get_auth_url,
    exchange_code,
    is_authenticated,
)
from src.ingest.yahoo.client import (
    get_user_leagues,
    get_my_team,
    get_league_standings,
    get_free_agents,
    get_matchup,
    get_trending_players,
)
from src.tools.fantasy.scoring import SKATER_WEIGHTS
from src.tools.schedule.models import RosterPlayer, RosterSlotSettings
from src.tools.schedule.optimizer import assign_players_to_slots
from src.api.stats_helpers import compute_fpts_per_gp

router = APIRouter()


@router.get("/status")
def yahoo_status():
    """Check if Yahoo is connected."""
    return {"connected": is_authenticated()}


@router.get("/connect")
def yahoo_connect():
    """Start OAuth flow — returns the Yahoo auth URL."""
    url = get_auth_url()
    return {"auth_url": url}


@router.get("/callback")
def yahoo_callback(code: str):
    """Handle OAuth callback — exchange code for tokens, redirect to frontend."""
    try:
        exchange_code(code)
        return RedirectResponse("http://localhost:5173/?yahoo=connected")
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/leagues")
def yahoo_leagues():
    """Get user's NHL fantasy leagues."""
    try:
        leagues = get_user_leagues()
        return {"leagues": leagues}
    except Exception as e:
        return {"error": str(e)}


@router.get("/team/{league_key}")
def yahoo_team(league_key: str):
    """Get user's team and roster in a league."""
    try:
        team = get_my_team(league_key)
        return team
    except Exception as e:
        return {"error": str(e)}


@router.get("/standings/{league_key}")
def yahoo_standings(league_key: str):
    """Get league standings."""
    try:
        standings = get_league_standings(league_key)
        return {"standings": standings}
    except Exception as e:
        return {"error": str(e)}


@router.get("/free-agents/{league_key}")
def yahoo_free_agents(league_key: str, count: int = 25):
    """Get available free agents."""
    try:
        players = get_free_agents(league_key, count=count)
        return {"players": players}
    except Exception as e:
        return {"error": str(e)}


@router.get("/matchup/{league_key}")
def yahoo_matchup(league_key: str, week: int = None):
    """Get current matchup."""
    try:
        matchup = get_matchup(league_key, week=week)
        return matchup
    except Exception as e:
        return {"error": str(e)}


@router.get("/trending/{league_key}")
def yahoo_trending(league_key: str, count: int = 20):
    """Get trending players — all players sorted by magnitude of ownership change."""
    try:
        from src.ingest.yahoo.auth import get_access_token
        import requests
        import xml.etree.ElementTree as ET

        token = get_access_token()
        if not token:
            return {"error": "Not authenticated"}

        ns = {"yh": "http://fantasysports.yahooapis.com/fantasy/v2/base.rng"}
        all_players = {}

        # Paginate through all players (Yahoo returns 25 per page)
        start = 0
        while True:
            url = (
                f"https://fantasysports.yahooapis.com/fantasy/v2"
                f"/league/{league_key}/players"
                f";status=ALL;out=percent_owned;start={start};count=25"
            )
            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code != 200:
                break

            root = ET.fromstring(resp.content)
            page_players = list(root.iter(f"{{{ns['yh']}}}player"))

            if not page_players:
                break

            for player_el in page_players:
                pk = player_el.findtext(f"yh:player_key", namespaces=ns)

                pct_el = player_el.find(f"yh:percent_owned", ns)
                pct_value = None
                pct_delta = None
                if pct_el is not None:
                    val = pct_el.findtext(f"yh:value", namespaces=ns)
                    delta = pct_el.findtext(f"yh:delta", namespaces=ns)
                    pct_value = int(val) if val else None
                    pct_delta = int(delta) if delta else None

                name_el = player_el.find(f"yh:name", ns)
                all_players[pk] = {
                    "player_key": pk,
                    "player_id": player_el.findtext(f"yh:player_id", namespaces=ns),
                    "name": name_el.findtext(f"yh:full", namespaces=ns) if name_el is not None else None,
                    "team": player_el.findtext(f"yh:editorial_team_abbr", namespaces=ns),
                    "position": player_el.findtext(f"yh:display_position", namespaces=ns),
                    "status": player_el.findtext(f"yh:status", namespaces=ns),
                    "percent_owned": pct_value,
                    "ownership_delta": pct_delta,
                }

            start += 25

        players = list(all_players.values())
        players.sort(key=lambda p: abs(p.get("ownership_delta") or 0), reverse=True)
        top = players[:count]

        # Resolve Yahoo names to our nhl_ids
        with get_session() as session:
            for p in top:
                try:
                    nhl_id = resolve_player(session, name=p["name"])
                    p["nhl_id"] = nhl_id
                except Exception:
                    p["nhl_id"] = None

        return {"players": top}
    except Exception as e:
        return {"error": str(e)}


@router.get("/optimal-adds/{league_key}")
def yahoo_optimal_adds(league_key: str, count: int = 50, season: str = "20242025", min_gp: int = 15):
    """Get free agents ranked by projected fantasy points per game.

    Fetches free agents from Yahoo, matches to our DB by name,
    computes FPTS/GP from their season stats using shared helper.
    """
    try:
        fa_players = get_free_agents(league_key, count=count)
    except Exception as e:
        return {"error": str(e)}

    results = []

    with get_session() as session:
        for fa in fa_players:
            name = fa.get("name")
            if not name:
                continue

            try:
                nhl_id = resolve_player(session, name=name)
            except Exception:
                continue

            if not nhl_id:
                continue

            fpts_data = compute_fpts_per_gp(session, nhl_id, season)
            if not fpts_data or fpts_data["gp"] < min_gp:
                continue

            results.append({
                "nhl_id": nhl_id,
                "yahoo_player_key": fa["player_key"],
                "name": name,
                "position": fa.get("position"),
                "team": fa.get("team"),
                "status": fa.get("status"),
                **fpts_data,
            })

    results.sort(key=lambda x: x["fpts_per_gp"], reverse=True)
    return {"players": results}


@router.get("/roster-week/{league_key}")
def yahoo_roster_week(league_key: str, season: str = "20242025"):
    """Get roster with this week's game schedule, slot analysis, and projections.

    Uses the schedule optimizer's slot assignment logic to show which
    positions can be filled each day and where streaming opportunities exist.
    """
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    week_dates = [monday + timedelta(days=i) for i in range(7)]

    try:
        yahoo_team = get_my_team(league_key)
    except Exception as e:
        return {"error": str(e)}

    if not yahoo_team:
        return {"error": "Could not find your team"}

    yahoo_roster = yahoo_team.get("roster", [])

    with get_session() as session:
        # Build team lookup
        all_teams = {t.abbrev: t.team_id for t in session.query(Team).all()}
        team_id_to_abbrev = {v: k for k, v in all_teams.items()}

        # Get all games this week, indexed by team
        week_games = (
            session.query(Game)
            .filter(Game.date >= monday, Game.date <= sunday)
            .all()
        )

        team_game_dates = {}
        for g in week_games:
            for tid in [g.home_team_id, g.away_team_id]:
                abbrev = team_id_to_abbrev.get(tid)
                if abbrev:
                    if abbrev not in team_game_dates:
                        team_game_dates[abbrev] = {}
                    opp_id = g.away_team_id if tid == g.home_team_id else g.home_team_id
                    opp_abbrev = team_id_to_abbrev.get(opp_id, "???")
                    is_home = tid == g.home_team_id
                    team_game_dates[abbrev][str(g.date)] = {
                        "opponent": opp_abbrev,
                        "is_home": is_home,
                    }

        # Build RosterPlayer objects for the optimizer + enrich with stats
        enriched_roster = []
        optimizer_players = []

        for p in yahoo_roster:
            player_team = p.get("team", "").upper()
            positions = [pos.strip() for pos in p.get("position", "").split(",")]

            # Resolve to our DB
            nhl_id = None
            try:
                nhl_id = resolve_player(session, name=p.get("name"))
            except Exception:
                pass

            # Get FPTS using shared helper
            fpts_data = None
            if nhl_id:
                fpts_data = compute_fpts_per_gp(session, nhl_id, season)

            # Build schedule for this player
            player_games = team_game_dates.get(player_team, {})
            schedule = []
            for d in week_dates:
                ds = str(d)
                game_info = player_games.get(ds)
                schedule.append({
                    "date": ds,
                    "has_game": game_info is not None,
                    "opponent": game_info["opponent"] if game_info else None,
                    "is_home": game_info["is_home"] if game_info else None,
                })

            # Build optimizer RosterPlayer
            rp = RosterPlayer(
                name=p.get("name", ""),
                team=player_team,
                positions=positions,
                nhl_id=nhl_id,
            )
            optimizer_players.append(rp)

            enriched_roster.append({
                "name": p.get("name"),
                "nhl_id": nhl_id,
                "position": p.get("position"),
                "selected_position": p.get("selected_position"),
                "team": player_team,
                "status": p.get("status"),
                "fpts_per_gp": fpts_data["fpts_per_gp"] if fpts_data else None,
                "avg_toi": fpts_data["avg_toi"] if fpts_data else None,
                "games_this_week": sum(1 for s in schedule if s["has_game"]),
                "schedule": schedule,
            })

    # Use optimizer to compute per-day slot assignments
    slot_settings = RosterSlotSettings()
    week_summary = []

    for d in week_dates:
        ds = str(d)
        day_name = d.strftime("%A")

        # Which players have games today?
        playing_today = [
            rp for rp, er in zip(optimizer_players, enriched_roster)
            if any(s["date"] == ds and s["has_game"] for s in er["schedule"])
            and not er.get("status")  # exclude injured
        ]

        # Run the optimizer's slot assignment
        assignments = assign_players_to_slots(playing_today, slot_settings)
        active_slots = slot_settings.active_slots()

        slots_used = {pos: len(assigned) for pos, assigned in assignments.items()}
        open_slots = [
            pos for pos, max_s in active_slots.items()
            if slots_used.get(pos, 0) < max_s
        ]

        week_summary.append({
            "date": ds,
            "day": day_name,
            "players_playing": len(playing_today),
            "slots_used": slots_used,
            "open_slots": open_slots,
        })

    return {
        "team_name": yahoo_team.get("name"),
        "week_start": str(monday),
        "week_end": str(sunday),
        "week_summary": week_summary,
        "roster": enriched_roster,
    }

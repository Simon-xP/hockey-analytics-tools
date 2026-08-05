"""News & injuries API — powered by Daily Faceoff + RSS feeds."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import text as sa_text

from src.api.schemas import (
    GoalieStartsResponse,
    StreamableGoaliesResponse,
    InjuriesResponse,
    TeamInjuriesResponse,
    NewsFeedResponse,
)

from src.core.db import get_session
from src.core.models import Player, Team
from src.core.resolver import resolve_player
from src.ingest.news.classifier import KNOWN_NON_PLAYERS
from src.ingest.news.rss import query_news
from src.ingest.news.injuries import current_injuries
from src.ingest.daily_faceoff.scraper import (
    scrape_goalie_starts,
    scrape_lines,
    TEAM_SLUGS,
)
from src.ingest.yahoo.auth import is_authenticated
from src.ingest.yahoo.client import get_user_leagues, get_my_team, get_league_standings
from src.api.routers.goalie_matchups import _compute_team_goalie_scores

router = APIRouter()


@router.get("/goalie-starts", response_model=GoalieStartsResponse)
def goalie_starts(date: str | None = None):
    """Get today's goalie starts with confirmation status."""
    try:
        starters = scrape_goalie_starts(date)
        return {"games": starters}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/streamable-goalies", response_model=StreamableGoaliesResponse)
def streamable_goalies(date: str | None = None):
    """Get goalie starts filtered to only unrostered goalies in user's league.

    Shows goalies that are free agents — the ones you could actually pick up.
    Each goalie includes their matchup opponent with team info.
    """
    try:
        starters = scrape_goalie_starts(date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # If not connected to Yahoo, return all goalies
    if not is_authenticated():
        all_goalies = []
        for g in starters:
            for side in ["home", "away"]:
                goalie = g[f"{side}_goalie"]
                opp_side = "away" if side == "home" else "home"
                if goalie and goalie.get("name"):
                    all_goalies.append({
                        "name": goalie["name"],
                        "team": g[f"{side}_team"],
                        "team_slug": g.get(f"{side}_team_slug"),
                        "opponent": g[f"{opp_side}_team"],
                        "opponent_slug": g.get(f"{opp_side}_team_slug"),
                        "is_home": side == "home",
                        "confirmation": goalie.get("confirmation"),
                        "sv_pct": goalie.get("sv_pct"),
                        "gaa": goalie.get("gaa"),
                        "wins": goalie.get("wins"),
                        "losses": goalie.get("losses"),
                    })
        return {"goalies": all_goalies, "filtered": False}

    # Get all rostered player names in one bulk call
    try:
        leagues = get_user_leagues()
        if not leagues:
            return {"goalies": [], "filtered": False}
        league_key = leagues[0]["league_key"]

        from src.ingest.yahoo.client import _get, _text, NS
        root = _get(f"/league/{league_key}/teams;out=roster")
        rostered_names = set()
        for player_el in root.iter(f"{{{NS['yh']}}}player"):
            name_el = player_el.find(f"yh:name", NS)
            if name_el is not None:
                name = _text(name_el, "full")
                if name:
                    rostered_names.add(name.lower())

    except Exception:
        rostered_names = set()

    # Get streamability rankings to sort by
    try:
        rankings = _compute_team_goalie_scores()
        # Map team abbrev -> full ranking data
        ranking_by_abbrev = {r["abbrev"]: r for r in rankings}
        stream_score_by_abbrev = {r["abbrev"]: r["opp_goalie_fpts_avg"] for r in rankings}
    except Exception:
        ranking_by_abbrev = {}
        stream_score_by_abbrev = {}

    # Slug to abbrev mapping
    slug_to_abbrev = {
        "anaheim-ducks": "ANA", "utah-hockey-club": "UTA", "boston-bruins": "BOS",
        "buffalo-sabres": "BUF", "calgary-flames": "CGY", "carolina-hurricanes": "CAR",
        "chicago-blackhawks": "CHI", "colorado-avalanche": "COL",
        "columbus-blue-jackets": "CBJ", "dallas-stars": "DAL",
        "detroit-red-wings": "DET", "edmonton-oilers": "EDM",
        "florida-panthers": "FLA", "los-angeles-kings": "LAK",
        "minnesota-wild": "MIN", "montreal-canadiens": "MTL",
        "nashville-predators": "NSH", "new-jersey-devils": "NJD",
        "new-york-islanders": "NYI", "new-york-rangers": "NYR",
        "ottawa-senators": "OTT", "philadelphia-flyers": "PHI",
        "pittsburgh-penguins": "PIT", "san-jose-sharks": "SJS",
        "seattle-kraken": "SEA", "st-louis-blues": "STL",
        "tampa-bay-lightning": "TBL", "toronto-maple-leafs": "TOR",
        "vancouver-canucks": "VAN", "vegas-golden-knights": "VGK",
        "washington-capitals": "WSH", "winnipeg-jets": "WPG",
    }

    # Filter to unrostered goalies
    streamable = []
    for g in starters:
        for side in ["home", "away"]:
            goalie = g[f"{side}_goalie"]
            opp_side = "away" if side == "home" else "home"
            if not goalie or not goalie.get("name"):
                continue
            if goalie["name"].lower() in rostered_names:
                continue

            # The goalie is facing the opponent team — score = how good opponent is to stream against
            opp_slug = g.get(f"{opp_side}_team_slug", "")
            opp_abbrev = slug_to_abbrev.get(opp_slug, "")
            stream_score = stream_score_by_abbrev.get(opp_abbrev, 0)

            # Get opponent offensive stats
            opp_ranking = ranking_by_abbrev.get(opp_abbrev, {})

            streamable.append({
                "name": goalie["name"],
                "team": g[f"{side}_team"],
                "team_slug": g.get(f"{side}_team_slug"),
                "opponent": g[f"{opp_side}_team"],
                "opponent_slug": g.get(f"{opp_side}_team_slug"),
                "opponent_abbrev": opp_abbrev,
                "is_home": side == "home",
                "confirmation": goalie.get("confirmation"),
                "sv_pct": goalie.get("sv_pct"),
                "gaa": goalie.get("gaa"),
                "wins": goalie.get("wins"),
                "losses": goalie.get("losses"),
                "stream_score": stream_score,
                "opp_goals_per_game": opp_ranking.get("goals_per_game"),
                "opp_ga_per_game": opp_ranking.get("goals_against_per_game"),
            })

    # Resolve goalie names to nhl_ids
    with get_session() as session:
        for g in streamable:
            try:
                g["nhl_id"] = resolve_player(session, name=g["name"])
            except Exception:
                g["nhl_id"] = None

    # Sort by stream score descending (best matchups first)
    streamable.sort(key=lambda x: x["stream_score"], reverse=True)

    return {"goalies": streamable, "filtered": True}


@router.get("/lines/{team_slug}")
def team_lines(team_slug: str):
    """Get line combinations for a team."""
    try:
        data = scrape_lines(team_slug)
        if not data:
            raise HTTPException(status_code=404, detail="Team not found")
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/injuries", response_model=InjuriesResponse)
def all_injuries(team: str | None = None):
    """Structured injury feed from the `player_injuries` table.

    Each row is enriched with:
    - `fpts_per_gp`: fantasy points per game (pre-injury)
    - `avg_toi`: average time on ice in minutes
    - `headshot`: player headshot URL
    - `soonest_return` / `latest_return`: estimated return window
    - `roster_status`: "mine" | "free" | null (if Yahoo connected)

    Sorted by soonest_return ascending (coming back soonest first),
    with nulls (season/unknown) at the end.
    """
    from src.core.queries.stats_helpers import compute_fpts_per_gp

    try:
        rows = current_injuries(team_abbrev=team)

        # Batch-compute FPPG and resolve roster status
        rostered_names = set()
        my_player_names = set()
        try:
            if is_authenticated():
                leagues = get_user_leagues()
                if leagues:
                    league_key = leagues[0]["league_key"]
                    from src.ingest.yahoo.client import _get, _text, NS
                    root = _get(f"/league/{league_key}/teams;out=roster")
                    my_team_key = None
                    try:
                        my_team = get_my_team(league_key)
                        my_team_key = my_team.get("team_key")
                    except Exception:
                        pass
                    for team_el in root.iter(f"{{{NS['yh']}}}team"):
                        team_key_el = team_el.find(f"yh:team_key", NS)
                        is_mine = my_team_key and team_key_el is not None and _text(team_key_el, ".") == my_team_key
                        for player_el in team_el.iter(f"{{{NS['yh']}}}player"):
                            name_el = player_el.find(f"yh:name", NS)
                            if name_el is not None:
                                name = _text(name_el, "full")
                                if name:
                                    rostered_names.add(name.lower())
                                    if is_mine:
                                        my_player_names.add(name.lower())
        except Exception:
            pass

        with get_session() as session:
            for r in rows:
                nhl_id = r.get("nhl_id")
                r["headshot"] = (
                    f"https://assets.nhle.com/mugs/nhl/latest/{nhl_id}.png"
                    if nhl_id else None
                )
                # FPPG
                if nhl_id:
                    fpts = compute_fpts_per_gp(session, nhl_id, season="20252026")
                    if fpts is None:
                        fpts = compute_fpts_per_gp(session, nhl_id, season="20242025")
                    r["fpts_per_gp"] = fpts["fpts_per_gp"] if fpts else None
                    r["avg_toi"] = fpts["avg_toi"] if fpts else None
                    r["gp"] = fpts["gp"] if fpts else None
                else:
                    r["fpts_per_gp"] = None
                    r["avg_toi"] = None
                    r["gp"] = None

                # Roster status
                pname = (r.get("player_name") or "").lower()
                if pname in my_player_names:
                    r["roster_status"] = "mine"
                elif rostered_names and pname not in rostered_names:
                    r["roster_status"] = "free"
                elif rostered_names:
                    r["roster_status"] = "rostered"
                else:
                    r["roster_status"] = None

        # Sort: soonest return first, nulls at end
        def sort_key(r):
            sr = r.get("soonest_return")
            if sr:
                return (0, sr)
            if r.get("severity") == "season":
                return (2, "")
            return (1, "")

        rows.sort(key=sort_key)
        return {"items": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/injuries/{team_slug}", response_model=TeamInjuriesResponse)
def team_injuries(team_slug: str):
    """Get injured players for a team."""
    try:
        data = scrape_lines(team_slug)
        if not data:
            raise HTTPException(status_code=404, detail="Team not found")

        injuries = [
            {
                "player": p["name"],
                "position": p["position"],
                "injury_status": p["injury_status"],
                "game_time_decision": p.get("game_time_decision", False),
                "news": p.get("news", {}).get("details") if p.get("news") else None,
            }
            for p in data["players"]
            if p.get("injury_status")
        ]

        return {
            "team": data["team_name"],
            "team_abbrev": data["team_abbrev"],
            "injuries": injuries,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _pick_best(session, matches: list, team_id: int | None) -> int | None:
    """From a list of Player matches, pick the best candidate.

    Prefers team-filtered results, then breaks ties by most GP this season.
    Returns nhl_id or None if no usable match.
    """
    if not matches:
        return None
    candidates = [m for m in matches if m.team_id == team_id] if team_id else matches
    if not candidates:
        candidates = matches
    if len(candidates) == 1:
        return candidates[0].nhl_id
    # Tiebreak: most points (goals + assists) this season
    from datetime import date
    today = date.today()
    start_year = today.year if today.month >= 10 else today.year - 1
    start_gid = start_year * 1_000_000
    end_gid = (start_year + 1) * 1_000_000
    best, best_pts = None, -1
    for p in candidates:
        pts = session.execute(
            sa_text(
                "SELECT COALESCE(SUM(goals + assists), 0) FROM game_advanced_stats "
                "WHERE player_id = :pid AND situation = 'all' "
                "AND game_id >= :s AND game_id < :e"
            ),
            {"pid": p.nhl_id, "s": start_gid, "e": end_gid},
        ).scalar() or 0
        if pts > best_pts:
            best, best_pts = p.nhl_id, pts
    return best if best_pts > 0 else None


def _resolve_player_id(session, name: str, team_abbrev: str | None = None) -> int | None:
    """Resolve a player name to an nhl_id, with single-name fallbacks.

    Handles full names, last-name-only, and first-name-only (when team
    context narrows it to one candidate). Filters out known coaches.
    """
    if not name:
        return None

    # Filter out known coaches/non-players
    if name.lower() in KNOWN_NON_PLAYERS:
        return None

    # Full-name resolution (exact + fuzzy)
    try:
        nhl_id = resolve_player(session, name=name, team_abbrev=team_abbrev)
        if nhl_id:
            return nhl_id
    except Exception:
        pass

    # Resolve team_abbrev → team_id for filtering
    team_id = None
    if team_abbrev:
        team = session.query(Team).filter(Team.abbrev == team_abbrev).first()
        if team:
            team_id = team.team_id

    parts = name.split()

    # Single-word name — could be first or last name
    if len(parts) == 1:
        token = parts[0]

        # Try last-name match first (more common in tweets)
        matches = (
            session.query(Player)
            .filter(Player.full_name.ilike(f"% {token}"))
            .all()
        )
        pick = _pick_best(session, matches, team_id)
        if pick:
            return pick

        # Try first-name match (e.g., "Luke" → Luke Hughes on NJD)
        matches = (
            session.query(Player)
            .filter(Player.full_name.ilike(f"{token} %"))
            .all()
        )
        pick = _pick_best(session, matches, team_id)
        if pick:
            return pick

        return None

    # Multi-word name that didn't match — try last-name fallback
    last_name = parts[-1]
    matches = (
        session.query(Player)
        .filter(Player.full_name.ilike(f"% {last_name}"))
        .all()
    )
    if team_id:
        team_matches = [m for m in matches if m.team_id == team_id]
        if len(team_matches) == 1:
            return team_matches[0].nhl_id
    if len(matches) == 1:
        return matches[0].nhl_id
    if len(matches) > 1 and parts[0]:
        for m in matches:
            if m.full_name.lower().startswith(parts[0].lower()):
                return m.nhl_id
    return None


@router.get("/feed", response_model=NewsFeedResponse)
def news_feed(limit: int = 20, offset: int = 0):
    """Get the latest classified NHL news from the DB.

    Each item is one source tweet containing one or more `snippets`
    (distinct fantasy-actionable facts). Reads from `news_items` —
    the ingester (scripts/ingest_news.py) is responsible for keeping
    it fresh.
    """
    try:
        items = query_news(limit=limit, offset=offset)

        with get_session() as session:
            for item in items:
                for snip in item.get("snippets", []):
                    name = snip.get("player_name")
                    team_tag = snip.get("team_tag")
                    nhl_id = _resolve_player_id(session, name, team_tag) if name else None
                    # Replace partial names in summary with the full resolved name
                    full_name = name
                    if nhl_id:
                        player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
                        if player and name and player.full_name.lower() != name.lower():
                            summary = snip.get("summary", "")
                            snip["summary"] = summary.replace(name, player.full_name, 1)
                            full_name = player.full_name
                    snip["player"] = {
                        "name": full_name,
                        "nhl_id": nhl_id,
                        "headshot": (
                            f"https://assets.nhle.com/mugs/nhl/latest/{nhl_id}.png"
                            if nhl_id else None
                        ),
                    }

        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

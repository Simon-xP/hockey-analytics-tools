"""News & injuries API — powered by Daily Faceoff + RSS feeds."""

from fastapi import APIRouter

from src.core.db import get_session
from src.core.models import Player
from src.core.resolver import resolve_player
from src.ingest.news.rss import fetch_news
from src.ingest.daily_faceoff.scraper import (
    scrape_goalie_starts,
    scrape_lines,
    TEAM_SLUGS,
)
from src.ingest.yahoo.auth import is_authenticated
from src.ingest.yahoo.client import get_user_leagues, get_my_team, get_league_standings
from src.api.routers.goalie_matchups import _compute_team_goalie_scores

router = APIRouter()


@router.get("/goalie-starts")
def goalie_starts(date: str | None = None):
    """Get today's goalie starts with confirmation status."""
    try:
        starters = scrape_goalie_starts(date)
        return {"games": starters}
    except Exception as e:
        return {"error": str(e)}


@router.get("/streamable-goalies")
def streamable_goalies(date: str | None = None):
    """Get goalie starts filtered to only unrostered goalies in user's league.

    Shows goalies that are free agents — the ones you could actually pick up.
    Each goalie includes their matchup opponent with team info.
    """
    try:
        starters = scrape_goalie_starts(date)
    except Exception as e:
        return {"error": str(e)}

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
            return {"error": "Team not found"}
        return data
    except Exception as e:
        return {"error": str(e)}


@router.get("/injuries/{team_slug}")
def team_injuries(team_slug: str):
    """Get injured players for a team."""
    try:
        data = scrape_lines(team_slug)
        if not data:
            return {"error": "Team not found"}

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
    except Exception as e:
        return {"error": str(e)}


@router.get("/feed")
def news_feed(limit: int = 20):
    """Get latest NHL news from GameDayTweets, classified and enriched."""
    try:
        items = fetch_news(limit=limit)

        # Resolve player names to nhl_ids for headshots
        with get_session() as session:
            # Get team tags to help disambiguate
            for item in items:
                enriched_players = []
                team_tags = item.get("team_tags", [])

                for name in item.get("players", []):
                    nhl_id = None
                    try:
                        nhl_id = resolve_player(session, name=name)
                    except Exception:
                        pass

                    # If resolve failed, try searching by partial name
                    if not nhl_id:
                        parts = name.split()
                        if len(parts) >= 2:
                            last_name = parts[-1]
                            matches = (
                                session.query(Player)
                                .filter(Player.full_name.ilike(f"%{last_name}%"))
                                .all()
                            )
                            if len(matches) == 1:
                                nhl_id = matches[0].nhl_id
                            elif len(matches) > 1 and parts[0]:
                                # Try first initial + last name
                                for m in matches:
                                    if m.full_name.startswith(parts[0]):
                                        nhl_id = m.nhl_id
                                        break

                    enriched_players.append({
                        "name": name,
                        "nhl_id": nhl_id,
                        "headshot": f"https://assets.nhle.com/mugs/nhl/latest/{nhl_id}.png" if nhl_id else None,
                    })
                item["players"] = enriched_players

        return {"items": items}
    except Exception as e:
        return {"error": str(e)}

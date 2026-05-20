"""Daily Faceoff scraper — line combinations, goalie starts, injuries.

Scrapes structured JSON from Daily Faceoff pages. No HTML parsing needed —
data is embedded as __NEXT_DATA__ in the page source.

Usage:
    from src.ingest.daily_faceoff.scraper import scrape_lines, scrape_goalie_starts

    lines = scrape_lines("toronto-maple-leafs")  # one team
    all_lines = scrape_all_lines()                # all 32 teams
    goalies = scrape_goalie_starts()              # today's starters
    goalies = scrape_goalie_starts("2026-04-05")  # specific date
"""

import json
import re
import time
from datetime import date

import httpx

BASE_URL = "https://www.dailyfaceoff.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
REQUEST_DELAY = 1.0

# Team slugs for all 32 NHL teams
TEAM_SLUGS = [
    "anaheim-ducks", "utah-hockey-club", "boston-bruins", "buffalo-sabres",
    "calgary-flames", "carolina-hurricanes", "chicago-blackhawks",
    "colorado-avalanche", "columbus-blue-jackets", "dallas-stars",
    "detroit-red-wings", "edmonton-oilers", "florida-panthers",
    "los-angeles-kings", "minnesota-wild", "montreal-canadiens",
    "nashville-predators", "new-jersey-devils", "new-york-islanders",
    "new-york-rangers", "ottawa-senators", "philadelphia-flyers",
    "pittsburgh-penguins", "san-jose-sharks", "seattle-kraken",
    "st-louis-blues", "tampa-bay-lightning", "toronto-maple-leafs",
    "vancouver-canucks", "vegas-golden-knights", "washington-capitals",
    "winnipeg-jets",
]


def _fetch_next_data(url: str) -> dict | None:
    """Fetch a Daily Faceoff page and extract the __NEXT_DATA__ JSON."""
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT})
    if resp.status_code != 200:
        return None

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text)
    if not m:
        return None

    return json.loads(m.group(1))


def scrape_lines(team_slug: str) -> dict | None:
    """Scrape line combinations for a single team.

    Returns dict with:
        team_name, team_abbrev, updated_at, source,
        players: list of {name, position, group, category, injury_status, news}
    """
    url = f"{BASE_URL}/teams/{team_slug}/line-combinations"
    data = _fetch_next_data(url)
    if not data:
        return None

    combos = data.get("props", {}).get("pageProps", {}).get("combinations", {})
    if not combos:
        return None

    players = []
    for p in combos.get("players", []):
        player = {
            "name": p.get("name"),
            "player_id": p.get("playerId"),
            "jersey_number": p.get("jerseyNumber"),
            "position": p.get("positionIdentifier"),
            "position_name": p.get("positionName"),
            "group": p.get("groupIdentifier"),
            "group_name": p.get("groupName"),
            "category": p.get("categoryIdentifier"),  # "ev", "pp", "pk"
            "category_name": p.get("categoryName"),
            "injury_status": p.get("injuryStatus"),
            "game_time_decision": p.get("gameTimeDecision", False),
        }

        news = p.get("latestNews")
        if news:
            player["news"] = {
                "details": news.get("details"),
                "created_at": news.get("createdAt"),
            }

        players.append(player)

    return {
        "team_name": combos.get("teamName"),
        "team_abbrev": combos.get("teamAbbreviation"),
        "updated_at": combos.get("updatedAt"),
        "source": combos.get("sourceName"),
        "players": players,
    }


def scrape_all_lines() -> list[dict]:
    """Scrape line combinations for all 32 teams."""
    results = []
    for i, slug in enumerate(TEAM_SLUGS):
        time.sleep(REQUEST_DELAY)
        team_data = scrape_lines(slug)
        if team_data:
            results.append(team_data)
            if (i + 1) % 8 == 0:
                print(f"  Scraped {i + 1}/{len(TEAM_SLUGS)} teams")
    return results


def scrape_goalie_starts(target_date: str | None = None) -> list[dict]:
    """Scrape today's (or a specific date's) goalie starts.

    Returns list of dicts, one per game:
        home_team, away_team,
        home_goalie: {name, record, sv_pct, gaa, confirmation, news},
        away_goalie: {name, record, sv_pct, gaa, confirmation, news}
    """
    if target_date is None:
        target_date = str(date.today())

    url = f"{BASE_URL}/starting-goalies/{target_date}"
    data = _fetch_next_data(url)
    if not data:
        return []

    entries = data.get("props", {}).get("pageProps", {}).get("data", [])

    games = []
    for g in entries:
        game = {
            "home_team": g.get("homeTeamName"),
            "home_team_abbrev": None,
            "away_team": g.get("awayTeamName"),
            "away_team_abbrev": None,
            "home_goalie": _parse_goalie(g, "home"),
            "away_goalie": _parse_goalie(g, "away"),
        }

        # Extract abbrev from slug if available
        home_slug = g.get("homeTeamSlug", "")
        away_slug = g.get("awayTeamSlug", "")
        game["home_team_slug"] = home_slug
        game["away_team_slug"] = away_slug

        games.append(game)

    return games


def _parse_goalie(game_data: dict, side: str) -> dict:
    """Parse goalie data from a game entry."""
    prefix = f"{side}Goalie"
    news_prefix = f"{side}News"

    return {
        "name": game_data.get(f"{prefix}Name"),
        "player_id": game_data.get(f"{prefix}Id"),
        "wins": game_data.get(f"{prefix}Wins"),
        "losses": game_data.get(f"{prefix}Losses"),
        "otl": game_data.get(f"{prefix}OvertimeLosses"),
        "shutouts": game_data.get(f"{prefix}Shutouts"),
        "sv_pct": game_data.get(f"{prefix}SavePercentage"),
        "gaa": game_data.get(f"{prefix}GoalsAgainstAvg"),
        "rating": game_data.get(f"{prefix}Rating"),
        "confirmation": game_data.get(f"{news_prefix}StrengthName"),  # "Confirmed", "Expected", "Likely", etc.
        "confirmation_id": game_data.get(f"{news_prefix}StrengthId"),
        "news": game_data.get(f"{news_prefix}Details"),
        "news_source": game_data.get(f"{news_prefix}SourceName"),
        "news_source_url": game_data.get(f"{news_prefix}SourceUrl"),
        "news_date": game_data.get(f"{news_prefix}CreatedAt"),
    }


def scrape_injuries() -> list[dict]:
    """Scrape injury data from all team line combination pages.

    Returns list of {team, team_abbrev, player, injury_status, news}.
    Only includes players with an injury status.
    """
    injuries = []
    for i, slug in enumerate(TEAM_SLUGS):
        time.sleep(REQUEST_DELAY)
        team_data = scrape_lines(slug)
        if not team_data:
            continue

        for p in team_data["players"]:
            if p.get("injury_status"):
                injury = {
                    "team": team_data["team_name"],
                    "team_abbrev": team_data["team_abbrev"],
                    "player": p["name"],
                    "position": p["position"],
                    "injury_status": p["injury_status"],
                    "game_time_decision": p.get("game_time_decision", False),
                }
                if p.get("news"):
                    injury["news"] = p["news"]["details"]
                    injury["news_date"] = p["news"]["created_at"]
                injuries.append(injury)

        if (i + 1) % 8 == 0:
            print(f"  Scraped {i + 1}/{len(TEAM_SLUGS)} teams for injuries")

    return injuries


if __name__ == "__main__":
    print("=== Goalie Starts (last game day) ===")
    starters = scrape_goalie_starts("2026-04-05")
    for g in starters:
        h = g["home_goalie"]
        a = g["away_goalie"]
        print(f'{a["name"]} ({a["confirmation"]}) @ {h["name"]} ({h["confirmation"]})')

    print(f"\n=== Line Combinations (TOR) ===")
    tor = scrape_lines("toronto-maple-leafs")
    if tor:
        ev_players = [p for p in tor["players"] if p["category"] == "ev"]
        for p in ev_players:
            status = f' [{p["injury_status"]}]' if p["injury_status"] else ""
            print(f'  {p["group"]:<4} {p["position"]:<3} {p["name"]}{status}')

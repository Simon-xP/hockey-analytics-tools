"""
Natural Stat Trick scraper.

Handles two types of data:
1. Season stats - aggregated stats per player per season (historical + current)
2. Game logs - per-game stats (current season only)

Usage:
    # Scrape historical season stats (2019-2024)
    python -m src.ingest.natural_stat_trick.scraper --historical

    # Scrape current season stats + game logs
    python -m src.ingest.natural_stat_trick.scraper --current

    # Scrape a specific situation
    python -m src.ingest.natural_stat_trick.scraper --situation 5v5_individual_counts
"""

import json
import random
import time
from datetime import date, datetime
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from src.core.db import get_session
from src.core.models import (
    Game,
    GameIndividualStats,
    GameOnIceStats,
    OnIceStats,
    Player,
    SeasonStats,
)
from src.core.resolver import resolve_player

CONFIG_PATH = Path(__file__).parent / "config.json"
BASE_URL = "http://www.naturalstattrick.com/playerteams.php"
GAME_LOG_BASE_URL = "http://www.naturalstattrick.com/playerreport.php"

# Rate limiting
REQUEST_DELAY = 10  # seconds between requests
GAME_LOG_DELAY_MIN = 20  # min seconds between game log requests
GAME_LOG_DELAY_MAX = 35  # max seconds (random jitter)
COOLDOWN_EVERY = 25  # longer pause every N requests
COOLDOWN_SECONDS = 120  # duration of longer pause

# Scrape state file for budget tracking and resumption
SCRAPE_STATE_PATH = Path(__file__).parents[3] / "data" / "scrape_state.json"

# Season definitions
HISTORICAL_SEASONS = list(range(2019, 2025))  # 2019-20 through 2024-25
CURRENT_SEASON = 2025  # 2025-26

# NST uses different team abbreviations than NHL API
NST_TEAM_MAP = {
    "S.J": "SJS",
    "L.A": "LAK",
    "N.J": "NJD",
    "T.B": "TBL",
}


# =============================================================================
# SHARED UTILITIES
# =============================================================================

def normalize_team_abbrev(abbrev: str) -> str:
    """Convert NST team abbreviation to standard NHL abbreviation."""
    if not abbrev:
        return abbrev
    first_team = abbrev.split(",")[0].strip()
    return NST_TEAM_MAP.get(first_team, first_team)


def safe_int(val) -> int | None:
    """Convert to int, return None if invalid."""
    try:
        return int(val) if pd.notna(val) else None
    except (ValueError, TypeError):
        return None


def safe_float(val) -> float | None:
    """Convert to float, return None if invalid."""
    try:
        return float(val) if pd.notna(val) else None
    except (ValueError, TypeError):
        return None


NST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.naturalstattrick.com/",
}


def fetch_html(url: str) -> str:
    """Fetch HTML from URL."""
    response = requests.get(url, headers=NST_HEADERS)
    response.raise_for_status()
    return response.text


def resolve_player_with_alias(session, player_name: str, team_abbrev: str) -> int | None:
    """Resolve player name to NHL ID, creating alias if found via fuzzy match."""
    lookup_team = normalize_team_abbrev(team_abbrev)
    return resolve_player(
        session,
        name=player_name,
        team_abbrev=lookup_team,
        create_alias=True,
        alias_source="naturalstattrick"
    )


# =============================================================================
# SEASON STATS
# =============================================================================

def build_season_stats_url(situation_config: dict, season: str) -> str:
    """Build NST URL for season stats."""
    params = {
        "fromseason": season,
        "thruseason": season,
        "stype": situation_config["stype"],
        "sit": situation_config["sit"],
        "score": situation_config["score"],
        "stdoi": situation_config["stdoi"],
        "rate": situation_config["rate"],
        "team": situation_config["team"],
        "pos": situation_config["pos"],
        "loc": situation_config["loc"],
        "toi": situation_config["toi"],
        "gpfilt": situation_config["gpfilt"],
        "fd": situation_config["fd"],
        "td": situation_config["td"],
        "tgp": situation_config["tgp"],
        "lines": situation_config["lines"],
        "draftteam": situation_config["draftteam"],
    }
    param_str = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{BASE_URL}?{param_str}"


def fetch_season_stats(situation_config: dict, season: str) -> pd.DataFrame:
    """Fetch season stats from NST."""
    url = build_season_stats_url(situation_config, season)
    html = fetch_html(url)
    df = pd.read_html(StringIO(html), header=0, index_col=0, na_values=["-"])[0]
    return df


def map_season_stats_to_records(df: pd.DataFrame, season: str, situation: str) -> tuple[list[dict], list[tuple]]:
    """
    Convert DataFrame to SeasonStats records.

    Returns (records, unresolved) where unresolved is list of (name, team) tuples.
    """
    records = []
    unresolved = []

    with get_session() as session:
        for _, row in df.iterrows():
            player_name = row.get("Player")
            team_abbrev = row.get("Team")

            if not player_name:
                continue

            nhl_id = resolve_player_with_alias(session, player_name, team_abbrev)

            if not nhl_id:
                unresolved.append((player_name, team_abbrev))
                continue

            record = {
                "nhl_id": nhl_id,
                "season": season,
                "situation": situation,
                "team_abbrev": team_abbrev,
                "position": row.get("Position"),
                "games_played": safe_int(row.get("GP")),
                "toi": safe_float(row.get("TOI")),
                "goals": safe_int(row.get("Goals")),
                "total_assists": safe_int(row.get("Total Assists")),
                "first_assists": safe_int(row.get("First Assists")),
                "second_assists": safe_int(row.get("Second Assists")),
                "total_points": safe_int(row.get("Total Points")),
                "ipp": safe_float(row.get("IPP")),
                "shots": safe_int(row.get("Shots")),
                "sh_pct": safe_float(row.get("SH%")),
                "ixg": safe_float(row.get("ixG")),
                "icf": safe_int(row.get("iCF")),
                "iff": safe_int(row.get("iFF")),
                "iscf": safe_int(row.get("iSCF")),
                "ihdcf": safe_int(row.get("iHDCF")),
                "rush_attempts": safe_int(row.get("Rush Attempts")),
                "rebounds_created": safe_int(row.get("Rebounds Created")),
                "pim": safe_int(row.get("PIM")),
                "total_penalties": safe_int(row.get("Total Penalties")),
                "penalties_drawn": safe_int(row.get("Penalties Drawn")),
                "giveaways": safe_int(row.get("Giveaways")),
                "takeaways": safe_int(row.get("Takeaways")),
                "hits": safe_int(row.get("Hits")),
                "hits_taken": safe_int(row.get("Hits Taken")),
                "shots_blocked": safe_int(row.get("Shots Blocked")),
                "faceoffs_won": safe_int(row.get("Faceoffs Won")),
                "faceoffs_lost": safe_int(row.get("Faceoffs Lost")),
                "faceoffs_pct": safe_float(row.get("Faceoffs %")),
            }
            records.append(record)

    return records, unresolved


def save_season_stats(records: list[dict]) -> int:
    """Save season stats to database (upsert). Returns count of new records."""
    if not records:
        return 0

    new_count = 0
    with get_session() as session:
        for record in records:
            existing = session.query(SeasonStats).filter(
                SeasonStats.nhl_id == record["nhl_id"],
                SeasonStats.season == record["season"],
                SeasonStats.situation == record["situation"]
            ).first()

            if existing:
                for key, value in record.items():
                    setattr(existing, key, value)
            else:
                session.add(SeasonStats(**record))
                new_count += 1

    return new_count


# =============================================================================
# ON-ICE STATS
# =============================================================================

def map_on_ice_stats_to_records(df: pd.DataFrame, season: str, situation: str) -> tuple[list[dict], list[tuple]]:
    """
    Convert DataFrame to OnIceStats records.

    Returns (records, unresolved) where unresolved is list of (name, team) tuples.
    """
    records = []
    unresolved = []

    with get_session() as session:
        for _, row in df.iterrows():
            player_name = row.get("Player")
            team_abbrev = row.get("Team")

            if not player_name:
                continue

            nhl_id = resolve_player_with_alias(session, player_name, team_abbrev)

            if not nhl_id:
                unresolved.append((player_name, team_abbrev))
                continue

            record = {
                "nhl_id": nhl_id,
                "season": season,
                "situation": situation,
                "team_abbrev": team_abbrev,
                "position": row.get("Position"),
                "games_played": safe_int(row.get("GP")),
                "toi": safe_float(row.get("TOI")),
                # Corsi
                "cf": safe_int(row.get("CF")),
                "ca": safe_int(row.get("CA")),
                "cf_pct": safe_float(row.get("CF%")),
                # Fenwick
                "ff": safe_int(row.get("FF")),
                "fa": safe_int(row.get("FA")),
                "ff_pct": safe_float(row.get("FF%")),
                # Shots
                "sf": safe_int(row.get("SF")),
                "sa": safe_int(row.get("SA")),
                "sf_pct": safe_float(row.get("SF%")),
                # Goals
                "gf": safe_int(row.get("GF")),
                "ga": safe_int(row.get("GA")),
                "gf_pct": safe_float(row.get("GF%")),
                # Expected goals
                "xgf": safe_float(row.get("xGF")),
                "xga": safe_float(row.get("xGA")),
                "xgf_pct": safe_float(row.get("xGF%")),
                # Scoring chances
                "scf": safe_int(row.get("SCF")),
                "sca": safe_int(row.get("SCA")),
                "scf_pct": safe_float(row.get("SCF%")),
                # High danger
                "hdcf": safe_int(row.get("HDCF")),
                "hdca": safe_int(row.get("HDCA")),
                "hdcf_pct": safe_float(row.get("HDCF%")),
                "hdgf": safe_int(row.get("HDGF")),
                "hdga": safe_int(row.get("HDGA")),
                "hdgf_pct": safe_float(row.get("HDGF%")),
                # Medium danger
                "mdcf": safe_int(row.get("MDCF")),
                "mdca": safe_int(row.get("MDCA")),
                "mdcf_pct": safe_float(row.get("MDCF%")),
                "mdgf": safe_int(row.get("MDGF")),
                "mdga": safe_int(row.get("MDGA")),
                "mdgf_pct": safe_float(row.get("MDGF%")),
                # Low danger
                "ldcf": safe_int(row.get("LDCF")),
                "ldca": safe_int(row.get("LDCA")),
                "ldcf_pct": safe_float(row.get("LDCF%")),
                "ldgf": safe_int(row.get("LDGF")),
                "ldga": safe_int(row.get("LDGA")),
                "ldgf_pct": safe_float(row.get("LDGF%")),
                # On-ice percentages
                "on_ice_sh_pct": safe_float(row.get("On-Ice SH%")),
                "on_ice_sv_pct": safe_float(row.get("On-Ice SV%")),
                "pdo": safe_float(row.get("PDO")),
                # Zone starts
                "off_zone_starts": safe_int(row.get("Off. Zone Starts")),
                "neu_zone_starts": safe_int(row.get("Neu. Zone Starts")),
                "def_zone_starts": safe_int(row.get("Def. Zone Starts")),
                "on_the_fly_starts": safe_int(row.get("On The Fly Starts")),
                "off_zone_start_pct": safe_float(row.get("Off. Zone Start %")),
                # Zone faceoffs
                "off_zone_faceoffs": safe_int(row.get("Off. Zone Faceoffs")),
                "neu_zone_faceoffs": safe_int(row.get("Neu. Zone Faceoffs")),
                "def_zone_faceoffs": safe_int(row.get("Def. Zone Faceoffs")),
                "off_zone_faceoff_pct": safe_float(row.get("Off. Zone Faceoff %")),
            }
            records.append(record)

    return records, unresolved


def save_on_ice_stats(records: list[dict]) -> int:
    """Save on-ice stats to database (upsert). Returns count of new records."""
    if not records:
        return 0

    new_count = 0
    with get_session() as session:
        for record in records:
            existing = session.query(OnIceStats).filter(
                OnIceStats.nhl_id == record["nhl_id"],
                OnIceStats.season == record["season"],
                OnIceStats.situation == record["situation"]
            ).first()

            if existing:
                for key, value in record.items():
                    setattr(existing, key, value)
            else:
                session.add(OnIceStats(**record))
                new_count += 1

    return new_count


def scrape_on_ice_stats(situation: str, seasons: list[int], delay: float = REQUEST_DELAY) -> dict:
    """
    Scrape on-ice stats for a situation across multiple seasons.

    Args:
        situation: Situation name from config (e.g., "5v5_on-ice_counts")
        seasons: List of season start years
        delay: Seconds between requests

    Returns:
        Dict with results summary
    """
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    if situation not in config:
        raise ValueError(f"Unknown situation: {situation}")

    situation_config = config[situation]
    total_records = 0
    total_new = 0
    total_unresolved = 0

    for i, year in enumerate(seasons):
        season_str = f"{year}{year + 1}"
        print(f"  [{i + 1}/{len(seasons)}] Fetching {situation} for {season_str}...")

        df = fetch_season_stats(situation_config, season_str)
        records, unresolved = map_on_ice_stats_to_records(df, season_str, situation)
        new_count = save_on_ice_stats(records)

        total_records += len(records)
        total_new += new_count
        total_unresolved += len(unresolved)

        print(f"       {len(records)} resolved, {len(unresolved)} unresolved, {new_count} new")

        if i < len(seasons) - 1:
            time.sleep(delay)

    return {
        "situation": situation,
        "seasons": len(seasons),
        "records": total_records,
        "new": total_new,
        "unresolved": total_unresolved
    }


# =============================================================================
# SCRAPE SEASON STATS (individual)
# =============================================================================

def scrape_season_stats(situation: str, seasons: list[int], delay: float = REQUEST_DELAY) -> dict:
    """
    Scrape season stats for a situation across multiple seasons.

    Args:
        situation: Situation name from config (e.g., "5v5_individual_counts")
        seasons: List of season start years (e.g., [2019, 2020, 2021])
        delay: Seconds between requests

    Returns:
        Dict with results summary
    """
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    if situation not in config:
        raise ValueError(f"Unknown situation: {situation}")

    situation_config = config[situation]
    total_records = 0
    total_new = 0
    total_unresolved = 0

    for i, year in enumerate(seasons):
        season_str = f"{year}{year + 1}"
        print(f"  [{i + 1}/{len(seasons)}] Fetching {situation} for {season_str}...")

        df = fetch_season_stats(situation_config, season_str)
        records, unresolved = map_season_stats_to_records(df, season_str, situation)
        new_count = save_season_stats(records)

        total_records += len(records)
        total_new += new_count
        total_unresolved += len(unresolved)

        print(f"       {len(records)} resolved, {len(unresolved)} unresolved, {new_count} new")

        if i < len(seasons) - 1:
            time.sleep(delay)

    return {
        "situation": situation,
        "seasons": len(seasons),
        "records": total_records,
        "new": total_new,
        "unresolved": total_unresolved
    }


# =============================================================================
# LAST N GAMES (rolling stats)
# =============================================================================

# L5 situations are defined in config.json with gpfilt=gpteam and tgp=5
# They use the same endpoint as season stats, just with different parameters.
# The scrape_current() function handles these automatically.


# =============================================================================
# HIGH-LEVEL ENTRY POINTS
# =============================================================================

def scrape_historical(situations: list[str] = None, delay: float = REQUEST_DELAY) -> dict:
    """
    Scrape historical season stats (2019-2024).

    Args:
        situations: List of situations to scrape, or None for all
        delay: Seconds between requests
    """
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    if situations is None:
        situations = list(config.keys())

    print(f"Scraping HISTORICAL data ({HISTORICAL_SEASONS[0]}-{HISTORICAL_SEASONS[-1] + 1})")
    print(f"Situations: {len(situations)}, Seasons: {len(HISTORICAL_SEASONS)}")
    print(f"Estimated requests: {len(situations) * len(HISTORICAL_SEASONS)}")
    print()

    results = {}
    for i, situation in enumerate(situations):
        print(f"[{i + 1}/{len(situations)}] {situation}")
        results[situation] = scrape_season_stats(situation, HISTORICAL_SEASONS, delay)

        if i < len(situations) - 1:
            print(f"  Waiting {delay}s before next situation...")
            time.sleep(delay)

    return results


def scrape_current(delay: float = REQUEST_DELAY) -> dict:
    """
    Scrape current season data (individual + on-ice, season + L5).

    Args:
        delay: Seconds between requests
    """
    # Individual stats (season-long)
    individual_season = [
        "5v5_individual_counts",
        "5v5_individual_rates",
        "all_individual_counts",
        "all_individual_rates",
        "pp_individual_counts",
    ]

    # Individual stats (last 5 games)
    individual_l5 = [
        "5v5_individual_counts_L5",
        "5v5_individual_rates_L5",
        "all_individual_counts_L5",
        "all_individual_rates_L5",
        "pp_individual_counts_L5",
    ]

    # On-ice stats (season-long)
    on_ice_season = [
        "5v5_on-ice_counts",
        "5v5_on-ice_rates",
        "all_on-ice_counts",
        "all_on-ice_rates",
    ]

    # On-ice stats (last 5 games)
    on_ice_l5 = [
        "5v5_on-ice_counts_L5",
        "all_on-ice_counts_L5",
    ]

    print(f"Scraping CURRENT SEASON ({CURRENT_SEASON}-{CURRENT_SEASON + 1})")
    print()

    results = {
        "individual_season": {},
        "individual_l5": {},
        "on_ice_season": {},
        "on_ice_l5": {},
    }

    # Individual season stats
    print("=== Individual Stats (Season) ===")
    for i, situation in enumerate(individual_season):
        print(f"[{i + 1}/{len(individual_season)}] {situation}")
        results["individual_season"][situation] = scrape_season_stats(situation, [CURRENT_SEASON], delay)
        if i < len(individual_season) - 1:
            time.sleep(delay)

    # Individual L5 stats
    print()
    print("=== Individual Stats (Last 5 Games) ===")
    for i, situation in enumerate(individual_l5):
        print(f"[{i + 1}/{len(individual_l5)}] {situation}")
        results["individual_l5"][situation] = scrape_season_stats(situation, [CURRENT_SEASON], delay)
        if i < len(individual_l5) - 1:
            time.sleep(delay)

    # On-ice season stats
    print()
    print("=== On-Ice Stats (Season) ===")
    for i, situation in enumerate(on_ice_season):
        print(f"[{i + 1}/{len(on_ice_season)}] {situation}")
        results["on_ice_season"][situation] = scrape_on_ice_stats(situation, [CURRENT_SEASON], delay)
        if i < len(on_ice_season) - 1:
            time.sleep(delay)

    # On-ice L5 stats
    print()
    print("=== On-Ice Stats (Last 5 Games) ===")
    for i, situation in enumerate(on_ice_l5):
        print(f"[{i + 1}/{len(on_ice_l5)}] {situation}")
        results["on_ice_l5"][situation] = scrape_on_ice_stats(situation, [CURRENT_SEASON], delay)
        if i < len(on_ice_l5) - 1:
            time.sleep(delay)

    return results


# =============================================================================
# GAME LOG SCRAPING (per-player, per-game stats)
# =============================================================================

# Game log situations to scrape — 1 request per player to minimize rate limiting
# sit=all captures all situations (5v5, PP, PK combined)
# stdoi=std gives individual stats (goals, assists, shots, ixG, iCF, etc.)
# On-ice stats (stdoi=oi) can be backfilled later if needed
GAME_LOG_SITUATIONS = [
    {"sit": "all", "stdoi": "std", "situation_name": "all"},
]


class ScrapeBudget:
    """Tracks daily request budget and scraping progress.

    Persists state to a JSON file so scraping can resume across runs.
    """

    def __init__(self, state_path: Path = SCRAPE_STATE_PATH):
        self.state_path = state_path
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if self.state_path.exists():
            with open(self.state_path, "r") as f:
                return json.load(f)
        return {"daily": {}, "progress": {}}

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump(self.state, f, indent=2)

    def requests_today(self) -> int:
        today = str(date.today())
        return self.state.get("daily", {}).get(today, 0)

    def increment(self):
        today = str(date.today())
        if "daily" not in self.state:
            self.state["daily"] = {}
        self.state["daily"][today] = self.state["daily"].get(today, 0) + 1
        self.save()

    def can_request(self, budget: int, run_count: int) -> bool:
        """Check if we can make another request within the per-run budget."""
        return run_count < budget

    def is_player_done(self, season: str, nhl_id: int, sit: str, stdoi: str) -> bool:
        key = f"{season}_{sit}_{stdoi}"
        done = self.state.get("progress", {}).get(key, [])
        return nhl_id in done

    def mark_player_done(self, season: str, nhl_id: int, sit: str, stdoi: str):
        key = f"{season}_{sit}_{stdoi}"
        if "progress" not in self.state:
            self.state["progress"] = {}
        if key not in self.state["progress"]:
            self.state["progress"][key] = []
        if nhl_id not in self.state["progress"][key]:
            self.state["progress"][key].append(nhl_id)
        self.save()

    def get_progress(self, season: str) -> dict[str, int]:
        """Get count of completed players per situation for a season."""
        result = {}
        for key, done_list in self.state.get("progress", {}).items():
            if key.startswith(season):
                result[key] = len(done_list)
        return result


def build_game_log_url(season: str, player_id: int, sit: str, stdoi: str) -> str:
    """Build NST game log URL for a specific player.

    Args:
        season: Season string like "20252026"
        player_id: NST player ID (same as NHL ID for most players)
        sit: Situation filter ("5v5", "all", "pp")
        stdoi: "std" for individual stats, "oi" for on-ice stats
    """
    params = {
        "fromseason": season,
        "thruseason": season,
        "stype": 2,
        "sit": sit,
        "stdoi": stdoi,
        "rate": "y",
        "v": "g",  # game log view
        "playerid": player_id,
    }
    param_str = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{GAME_LOG_BASE_URL}?{param_str}"


def fetch_game_log(season: str, player_id: int, sit: str, stdoi: str) -> pd.DataFrame | None:
    """Fetch game log from NST for a single player/situation.

    Returns DataFrame with one row per game, or None if no data / error.
    """
    url = build_game_log_url(season, player_id, sit, stdoi)
    try:
        html = fetch_html(url)
        tables = pd.read_html(StringIO(html), header=0, na_values=["-"])
        if not tables or tables[0].empty:
            return None
        return tables[0]
    except Exception as e:
        if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
            status = e.response.status_code
            if status in (429, 403):
                raise  # Let caller handle rate limiting
        print(f"    Warning: failed to fetch game log for player {player_id}: {e}")
        return None


def _resolve_game_id(session, game_date: date, team_abbrev: str) -> int | None:
    """Look up game_id from date and team abbreviation."""
    from src.core.models import Team
    team = session.query(Team).filter(Team.abbrev == team_abbrev).first()
    if not team:
        return None
    game = session.query(Game).filter(
        Game.date == game_date,
        (Game.home_team_id == team.team_id) | (Game.away_team_id == team.team_id)
    ).first()
    return game.game_id if game else None


def _parse_game_date(date_str: str) -> date | None:
    """Parse date from NST game log (format: '2025-04-16 EDM at S.J')."""
    try:
        # NST game column includes matchup info after the date
        date_part = str(date_str).strip().split()[0]
        return datetime.strptime(date_part, "%Y-%m-%d").date()
    except (ValueError, TypeError, IndexError):
        return None


def _parse_opponent_from_game_col(game_str: str, team_abbrev: str) -> str | None:
    """Parse opponent from NST Game column (e.g., '2024-10-09 WPG at EDM').

    Format is: 'YYYY-MM-DD AWAY at HOME'. The team_abbrev tells us which
    side this player is on; the other team is the opponent.
    """
    try:
        parts = game_str.strip().split()
        # parts: ['2024-10-09', 'WPG', 'at', 'EDM']
        if len(parts) >= 4 and parts[2] == "at":
            away_team = normalize_team_abbrev(parts[1])
            home_team = normalize_team_abbrev(parts[3])
            if team_abbrev == away_team:
                return home_team
            elif team_abbrev == home_team:
                return away_team
    except (IndexError, AttributeError):
        pass
    return None


def _parse_is_home_from_game_col(game_str: str, team_abbrev: str) -> bool | None:
    """Parse home/away from NST Game column (format: 'YYYY-MM-DD AWAY at HOME')."""
    try:
        parts = game_str.strip().split()
        if len(parts) >= 4 and parts[2] == "at":
            home_team = normalize_team_abbrev(parts[3])
            return team_abbrev == home_team
    except (IndexError, AttributeError):
        pass
    return None


def _determine_home_away(session, game_id: int, team_abbrev: str) -> bool | None:
    """Determine if team was home for a given game."""
    from src.core.models import Team
    game = session.query(Game).filter(Game.game_id == game_id).first()
    if not game:
        return None
    team = session.query(Team).filter(Team.abbrev == team_abbrev).first()
    if not team:
        return None
    return game.home_team_id == team.team_id


def _get_opponent(session, game_id: int, team_abbrev: str) -> str | None:
    """Get opponent abbreviation for a given game and team."""
    from src.core.models import Team
    game = session.query(Game).filter(Game.game_id == game_id).first()
    if not game:
        return None
    team = session.query(Team).filter(Team.abbrev == team_abbrev).first()
    if not team:
        return None
    opp_team_id = game.away_team_id if game.home_team_id == team.team_id else game.home_team_id
    opp_team = session.query(Team).filter(Team.team_id == opp_team_id).first()
    return opp_team.abbrev if opp_team else None


def map_game_individual_to_records(
    df: pd.DataFrame, nhl_id: int, season: str, situation: str
) -> list[dict]:
    """Convert game log DataFrame to GameIndividualStats records.

    NST rate=y returns per-60 values. Column headers include "/60" suffix
    (e.g., "Goals/60", "Shots/60"). Percentages keep their original names.
    """
    records = []
    with get_session() as session:
        for _, row in df.iterrows():
            game_date = _parse_game_date(row.get("Game"))
            if not game_date:
                continue

            team_abbrev = normalize_team_abbrev(str(row.get("Team", "")))
            game_id = _resolve_game_id(session, game_date, team_abbrev)

            # Use DB lookups when game_id available, else parse from NST
            if game_id:
                is_home = _determine_home_away(session, game_id, team_abbrev)
                opponent = _get_opponent(session, game_id, team_abbrev)
            else:
                game_str = str(row.get("Game", ""))
                is_home = _parse_is_home_from_game_col(game_str, team_abbrev)
                opponent = _parse_opponent_from_game_col(game_str, team_abbrev)

            record = {
                "nhl_id": nhl_id,
                "game_id": game_id,
                "game_date": game_date,
                "season": season,
                "situation": situation,
                "team_abbrev": team_abbrev,
                "opponent_abbrev": opponent,
                "is_home": is_home,
                # Raw TOI (not per-60)
                "toi": safe_float(row.get("TOI")),
                # Scoring (per 60)
                "goals_per_60": safe_float(row.get("Goals/60")),
                "total_assists_per_60": safe_float(row.get("Total Assists/60")),
                "first_assists_per_60": safe_float(row.get("First Assists/60")),
                "second_assists_per_60": safe_float(row.get("Second Assists/60")),
                "total_points_per_60": safe_float(row.get("Total Points/60")),
                # Percentages (not per-60)
                "ipp": safe_float(row.get("IPP")),
                "sh_pct": safe_float(row.get("S%")),
                # Shooting (per 60)
                "shots_per_60": safe_float(row.get("Shots/60")),
                "ixg_per_60": safe_float(row.get("ixG/60")),
                # Chances (per 60)
                "icf_per_60": safe_float(row.get("iCF/60")),
                "iff_per_60": safe_float(row.get("iFF/60")),
                "iscf_per_60": safe_float(row.get("iSCF/60")),
                "ihdcf_per_60": safe_float(row.get("iHDCF/60")),
                # Other (per 60)
                "rush_attempts_per_60": safe_float(row.get("Rush Attempts/60")),
                "rebounds_created_per_60": safe_float(row.get("Rebounds Created/60")),
                "pim_per_60": safe_float(row.get("PIM/60")),
                "total_penalties_per_60": safe_float(row.get("Total Penalties/60")),
                "penalties_drawn_per_60": safe_float(row.get("Penalties Drawn/60")),
                "giveaways_per_60": safe_float(row.get("Giveaways/60")),
                "takeaways_per_60": safe_float(row.get("Takeaways/60")),
                "hits_per_60": safe_float(row.get("Hits/60")),
                "hits_taken_per_60": safe_float(row.get("Hits Taken/60")),
                "shots_blocked_per_60": safe_float(row.get("Shots Blocked/60")),
                "faceoffs_won_per_60": safe_float(row.get("Faceoffs Won/60")),
                "faceoffs_lost_per_60": safe_float(row.get("Faceoffs Lost/60")),
            }
            records.append(record)

    return records


def map_game_on_ice_to_records(
    df: pd.DataFrame, nhl_id: int, season: str, situation: str
) -> list[dict]:
    """Convert game log DataFrame to GameOnIceStats records.

    NST rate=y returns per-60 values for on-ice stats.
    Column headers include "/60" suffix. Percentage columns unchanged.
    NST uses non-breaking spaces in headers; pandas normalizes to regular spaces.
    """
    records = []
    with get_session() as session:
        for _, row in df.iterrows():
            game_date = _parse_game_date(row.get("Game"))
            if not game_date:
                continue

            team_abbrev = normalize_team_abbrev(str(row.get("Team", "")))
            game_id = _resolve_game_id(session, game_date, team_abbrev)

            if game_id:
                is_home = _determine_home_away(session, game_id, team_abbrev)
                opponent = _get_opponent(session, game_id, team_abbrev)
            else:
                game_str = str(row.get("Game", ""))
                is_home = _parse_is_home_from_game_col(game_str, team_abbrev)
                opponent = _parse_opponent_from_game_col(game_str, team_abbrev)

            record = {
                "nhl_id": nhl_id,
                "game_id": game_id,
                "game_date": game_date,
                "season": season,
                "situation": situation,
                "team_abbrev": team_abbrev,
                "opponent_abbrev": opponent,
                "is_home": is_home,
                # Raw TOI (not per-60)
                "toi": safe_float(row.get("TOI")),
                # Corsi (per 60 + percentage)
                "cf_per_60": safe_float(row.get("CF/60")),
                "ca_per_60": safe_float(row.get("CA/60")),
                "cf_pct": safe_float(row.get("CF%")),
                # Fenwick (per 60 + percentage)
                "ff_per_60": safe_float(row.get("FF/60")),
                "fa_per_60": safe_float(row.get("FA/60")),
                "ff_pct": safe_float(row.get("FF%")),
                # Shots (per 60 + percentage)
                "sf_per_60": safe_float(row.get("SF/60")),
                "sa_per_60": safe_float(row.get("SA/60")),
                "sf_pct": safe_float(row.get("SF%")),
                # Goals (per 60 + percentage)
                "gf_per_60": safe_float(row.get("GF/60")),
                "ga_per_60": safe_float(row.get("GA/60")),
                "gf_pct": safe_float(row.get("GF%")),
                # Expected goals (per 60 + percentage)
                "xgf_per_60": safe_float(row.get("xGF/60")),
                "xga_per_60": safe_float(row.get("xGA/60")),
                "xgf_pct": safe_float(row.get("xGF%")),
                # Scoring chances (per 60 + percentage)
                "scf_per_60": safe_float(row.get("SCF/60")),
                "sca_per_60": safe_float(row.get("SCA/60")),
                "scf_pct": safe_float(row.get("SCF%")),
                # High danger (per 60 + percentage)
                "hdcf_per_60": safe_float(row.get("HDCF/60")),
                "hdca_per_60": safe_float(row.get("HDCA/60")),
                "hdcf_pct": safe_float(row.get("HDCF%")),
                "hdgf_per_60": safe_float(row.get("HDGF/60")),
                "hdga_per_60": safe_float(row.get("HDGA/60")),
                "hdgf_pct": safe_float(row.get("HDGF%")),
                # Medium danger (per 60 + percentage)
                "mdcf_per_60": safe_float(row.get("MDCF/60")),
                "mdca_per_60": safe_float(row.get("MDCA/60")),
                "mdcf_pct": safe_float(row.get("MDCF%")),
                "mdgf_per_60": safe_float(row.get("MDGF/60")),
                "mdga_per_60": safe_float(row.get("MDGA/60")),
                "mdgf_pct": safe_float(row.get("MDGF%")),
                # Low danger (per 60 + percentage)
                "ldcf_per_60": safe_float(row.get("LDCF/60")),
                "ldca_per_60": safe_float(row.get("LDCA/60")),
                "ldcf_pct": safe_float(row.get("LDCF%")),
                "ldgf_per_60": safe_float(row.get("LDGF/60")),
                "ldga_per_60": safe_float(row.get("LDGA/60")),
                "ldgf_pct": safe_float(row.get("LDGF%")),
                # On-ice percentages (not per-60)
                "on_ice_sh_pct": safe_float(row.get("On-Ice SH%")),
                "on_ice_sv_pct": safe_float(row.get("On-Ice SV%")),
                "pdo": safe_float(row.get("PDO")),
                # Zone starts (per 60) — NST uses \xa0 (non-breaking space)
                "off_zone_starts_per_60": safe_float(
                    row.get("Off.\xa0Zone Starts/60", row.get("Off. Zone Starts/60"))
                ),
                "neu_zone_starts_per_60": safe_float(
                    row.get("Neu.\xa0Zone Starts/60", row.get("Neu. Zone Starts/60"))
                ),
                "def_zone_starts_per_60": safe_float(
                    row.get("Def.\xa0Zone Starts/60", row.get("Def. Zone Starts/60"))
                ),
                "on_the_fly_starts_per_60": safe_float(
                    row.get("On\xa0The\xa0Fly Starts/60", row.get("On The Fly Starts/60"))
                ),
                # Zone start percentage (not per-60)
                "off_zone_start_pct": safe_float(
                    row.get("Off.\xa0Zone Start %", row.get("Off. Zone Start %"))
                ),
            }
            records.append(record)

    return records


def save_game_individual_stats(records: list[dict]) -> int:
    """Save game individual stats to database (upsert). Returns count of new records."""
    if not records:
        return 0

    new_count = 0
    with get_session() as session:
        for record in records:
            existing = session.query(GameIndividualStats).filter(
                GameIndividualStats.nhl_id == record["nhl_id"],
                GameIndividualStats.game_date == record["game_date"],
                GameIndividualStats.situation == record["situation"]
            ).first()

            if existing:
                for key, value in record.items():
                    setattr(existing, key, value)
            else:
                session.add(GameIndividualStats(**record))
                new_count += 1

    return new_count


def save_game_on_ice_stats(records: list[dict]) -> int:
    """Save game on-ice stats to database (upsert). Returns count of new records."""
    if not records:
        return 0

    new_count = 0
    with get_session() as session:
        for record in records:
            existing = session.query(GameOnIceStats).filter(
                GameOnIceStats.nhl_id == record["nhl_id"],
                GameOnIceStats.game_date == record["game_date"],
                GameOnIceStats.situation == record["situation"]
            ).first()

            if existing:
                for key, value in record.items():
                    setattr(existing, key, value)
            else:
                session.add(GameOnIceStats(**record))
                new_count += 1

    return new_count


def _game_log_delay(request_count: int):
    """Sleep with jitter, plus periodic cooldown pauses."""
    if request_count > 0 and request_count % COOLDOWN_EVERY == 0:
        print(f"    Cooldown pause ({COOLDOWN_SECONDS}s after {request_count} requests)...")
        time.sleep(COOLDOWN_SECONDS)
    else:
        delay = random.uniform(GAME_LOG_DELAY_MIN, GAME_LOG_DELAY_MAX)
        time.sleep(delay)


def scrape_player_game_logs(
    nhl_id: int, season: str, situations: list[dict] = None
) -> dict:
    """Scrape all game log situations for a single player.

    Args:
        nhl_id: NHL player ID (used as NST player ID)
        season: Season string like "20252026"
        situations: List of situation dicts, or None for default GAME_LOG_SITUATIONS

    Returns:
        Dict with counts of records saved per situation type
    """
    if situations is None:
        situations = GAME_LOG_SITUATIONS

    results = {"individual": 0, "on_ice": 0, "games": 0}

    for sit_config in situations:
        sit = sit_config["sit"]
        stdoi = sit_config["stdoi"]
        situation_name = sit_config["situation_name"]

        df = fetch_game_log(season, nhl_id, sit, stdoi)
        if df is None or df.empty:
            continue

        if stdoi == "std":
            records = map_game_individual_to_records(df, nhl_id, season, situation_name)
            new = save_game_individual_stats(records)
            results["individual"] += new
            results["games"] = max(results["games"], len(records))
        else:
            records = map_game_on_ice_to_records(df, nhl_id, season, situation_name)
            new = save_game_on_ice_stats(records)
            results["on_ice"] += new

    return results


def get_players_to_scrape(season: str) -> list[tuple[int, str, int]]:
    """Get list of (nhl_id, name, games_played) for players to scrape, ordered by GP desc.

    Uses SeasonStats as the source of which players played in the season.
    """
    with get_session() as session:
        stats = session.query(
            SeasonStats.nhl_id,
            Player.full_name,
            SeasonStats.games_played,
        ).join(
            Player, SeasonStats.nhl_id == Player.nhl_id
        ).filter(
            SeasonStats.season == season,
            SeasonStats.situation == "all_individual_counts",
            SeasonStats.games_played > 0,
        ).order_by(
            SeasonStats.games_played.desc()
        ).all()

        return [(s.nhl_id, s.full_name, s.games_played or 0) for s in stats]


def scrape_all_game_logs(
    season_year: int,
    budget: int = 100,
    player_id: int = None,
) -> dict:
    """Scrape game logs for all players in a season, with budget and resumption.

    Args:
        season_year: Season start year (e.g., 2025 for 2025-26)
        budget: Max requests for this run
        player_id: If set, only scrape this player (for testing)

    Returns:
        Dict with summary stats
    """
    season = f"{season_year}{season_year + 1}"
    tracker = ScrapeBudget()

    if player_id:
        players = [(player_id, f"Player {player_id}", 0)]
    else:
        players = get_players_to_scrape(season)
        if not players:
            print(f"No players found for season {season}. Run season stats scraper first.")
            return {"error": "no_players"}

    total_individual = 0
    total_on_ice = 0
    players_done = 0
    players_skipped = 0
    request_count = 0

    consecutive_failures = 0

    print(f"Game log scraping for {season} ({len(players)} players, budget={budget})")
    print(f"Requests used today: {tracker.requests_today()}")
    print()

    for nhl_id, name, gp in players:
        if not tracker.can_request(budget, request_count):
            print(f"\nBudget reached ({budget} requests). Stopping.")
            break

        # Check if all situations are done for this player
        all_done = all(
            tracker.is_player_done(season, nhl_id, s["sit"], s["stdoi"])
            for s in GAME_LOG_SITUATIONS
        )
        if all_done:
            players_skipped += 1
            continue

        print(f"  [{players_done + players_skipped + 1}/{len(players)}] {name} (GP: {gp})")

        for sit_config in GAME_LOG_SITUATIONS:
            sit = sit_config["sit"]
            stdoi = sit_config["stdoi"]
            situation_name = sit_config["situation_name"]

            if tracker.is_player_done(season, nhl_id, sit, stdoi):
                continue

            if not tracker.can_request(budget, request_count):
                break

            _game_log_delay(request_count)

            try:
                df = fetch_game_log(season, nhl_id, sit, stdoi)
                tracker.increment()
                request_count += 1
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if hasattr(e, 'response') else None
                if status in (429, 403):
                    print(f"\n    Rate limited (HTTP {status}). Stopping and saving progress.")
                    tracker.save()
                    return {
                        "stopped": "rate_limited",
                        "individual": total_individual,
                        "on_ice": total_on_ice,
                        "players_done": players_done,
                        "requests": request_count,
                    }
                raise

            if df is None or df.empty:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    print(f"\n    {consecutive_failures} consecutive failures. "
                          "NST may be blocking us. Stopping.")
                    tracker.save()
                    return {
                        "stopped": "connection_errors",
                        "individual": total_individual,
                        "on_ice": total_on_ice,
                        "players_done": players_done,
                        "requests": request_count,
                    }
                continue  # Don't mark as done

            # Success — reset failure counter and save data
            consecutive_failures = 0

            if stdoi == "std":
                records = map_game_individual_to_records(df, nhl_id, season, situation_name)
                new = save_game_individual_stats(records)
                total_individual += new
            else:
                records = map_game_on_ice_to_records(df, nhl_id, season, situation_name)
                new = save_game_on_ice_stats(records)
                total_on_ice += new

            tracker.mark_player_done(season, nhl_id, sit, stdoi)

        players_done += 1

    print(f"\nDone! {players_done} players scraped, {players_skipped} skipped (already done)")
    print(f"  Individual records: {total_individual}")
    if total_on_ice:
        print(f"  On-ice records: {total_on_ice}")
    print(f"  Requests used: {request_count}")

    return {
        "individual": total_individual,
        "on_ice": total_on_ice,
        "players_done": players_done,
        "players_skipped": players_skipped,
        "requests": request_count,
    }


def game_log_status(season_year: int = None) -> dict:
    """Print and return scraping progress status."""
    tracker = ScrapeBudget()

    if season_year:
        seasons = [season_year]
    else:
        seasons = [CURRENT_SEASON] + HISTORICAL_SEASONS

    status = {}
    for year in seasons:
        season = f"{year}{year + 1}"
        progress = tracker.get_progress(season)
        if progress:
            status[season] = progress
            player_list = get_players_to_scrape(season)
            total_players = len(player_list) if player_list else 0
            print(f"\n{season}:")
            for key, count in sorted(progress.items()):
                print(f"  {key}: {count}/{total_players} players done")

    if not status:
        print("No scraping progress recorded yet.")

    print(f"\nRequests used today: {tracker.requests_today()}")
    return status


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Natural Stat Trick scraper")
    parser.add_argument("--historical", action="store_true", help="Scrape historical data (2019-2024)")
    parser.add_argument("--current", action="store_true", help="Scrape current season data")
    parser.add_argument("--situation", type=str, help="Scrape a specific situation")
    parser.add_argument("--seasons", type=str, help="Comma-separated season years (e.g., 2023,2024)")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY, help=f"Delay between requests (default: {REQUEST_DELAY}s)")
    parser.add_argument("--list", action="store_true", help="List available situations")

    args = parser.parse_args()

    if args.list:
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
        print("Available situations:")
        for name in config.keys():
            print(f"  - {name}")
    elif args.historical:
        results = scrape_historical(delay=args.delay)
        print("\n=== Summary ===")
        for sit, data in results.items():
            print(f"{sit}: {data['records']} records ({data['new']} new)")
    elif args.current:
        results = scrape_current(delay=args.delay)
        print("\n=== Summary ===")
        for sit, data in results["season_stats"].items():
            print(f"{sit}: {data['records']} records ({data['new']} new)")
    elif args.situation:
        seasons = [int(s) for s in args.seasons.split(",")] if args.seasons else [CURRENT_SEASON]
        result = scrape_season_stats(args.situation, seasons, args.delay)
        print(f"\nDone! {result['records']} records ({result['new']} new)")
    else:
        parser.print_help()

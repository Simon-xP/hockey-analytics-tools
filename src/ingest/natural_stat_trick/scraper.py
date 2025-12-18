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
import time
import requests
import pandas as pd
from io import StringIO
from pathlib import Path

from src.core.db import get_session
from src.core.models import SeasonStats, OnIceStats
from src.core.resolver import resolve_player

CONFIG_PATH = Path(__file__).parent / "config.json"
BASE_URL = "http://www.naturalstattrick.com/playerteams.php"

# Rate limiting
REQUEST_DELAY = 10  # seconds between requests

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


def fetch_html(url: str) -> str:
    """Fetch HTML from URL."""
    response = requests.get(url)
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

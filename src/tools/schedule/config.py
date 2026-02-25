"""Load and save roster configuration from JSON."""

import json
from pathlib import Path
from typing import Optional

from src.core.db import get_session
from src.core.resolver import resolve_player
from src.tools.schedule.models import RosterSlotSettings, RosterPlayer, Roster


CONFIG_DIR = Path(__file__).parents[3] / "config"
DEFAULT_ROSTER_PATH = CONFIG_DIR / "roster.json"


def load_roster(path: Path = DEFAULT_ROSTER_PATH, resolve_ids: bool = True) -> Roster:
    """
    Load roster from JSON config file.

    Args:
        path: Path to roster JSON file
        resolve_ids: If True, resolve player names to NHL IDs

    Returns:
        Roster object with players and league settings
    """
    with open(path) as f:
        data = json.load(f)

    # Parse league settings
    settings_data = data.get("league_settings", {})
    roster_slot_settings = RosterSlotSettings(
        c=settings_data.get("c", 2),
        lw=settings_data.get("lw", 2),
        rw=settings_data.get("rw", 2),
        d=settings_data.get("d", 4),
        g=settings_data.get("g", 2),
        util=settings_data.get("util", 1),
        bn=settings_data.get("bn", 4),
        ir=settings_data.get("ir", 2),
        ir_plus=settings_data.get("ir_plus", 2),
    )

    # Parse players
    players = []
    for p in data.get("players", []):
        player = RosterPlayer(
            name=p["name"],
            team=p["team"],
            positions=p["positions"],
            nhl_id=p.get("nhl_id"),
        )
        players.append(player)

    # Resolve NHL IDs if requested
    if resolve_ids:
        players = _resolve_player_ids(players)

    return Roster(players=players, roster_slot_settings=roster_slot_settings)


def _resolve_player_ids(players: list[RosterPlayer]) -> list[RosterPlayer]:
    """Resolve player names to NHL IDs using the player resolver."""
    with get_session() as session:
        for player in players:
            if player.nhl_id is None:
                try:
                    nhl_id = resolve_player(
                        session,
                        name=player.name,
                        team_abbrev=player.team,
                    )
                    player.nhl_id = nhl_id
                except Exception as e:
                    print(f"Warning: Could not resolve {player.name} ({player.team}): {e}")

    return players


def save_roster(roster: Roster, path: Path = DEFAULT_ROSTER_PATH) -> None:
    """
    Save roster to JSON config file.

    Args:
        roster: Roster object to save
        path: Path to write JSON file
    """
    data = {
        "league_settings": {
            "c": roster.roster_slot_settings.c,
            "lw": roster.roster_slot_settings.lw,
            "rw": roster.roster_slot_settings.rw,
            "d": roster.roster_slot_settings.d,
            "g": roster.roster_slot_settings.g,
            "util": roster.roster_slot_settings.util,
            "bn": roster.roster_slot_settings.bn,
            "ir": roster.roster_slot_settings.ir,
            "ir_plus": roster.roster_slot_settings.ir_plus,
        },
        "players": [
            {
                "name": p.name,
                "team": p.team,
                "positions": p.positions,
                "nhl_id": p.nhl_id,
            }
            for p in roster.players
        ],
    }

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def add_player(
    name: str,
    team: str,
    positions: list[str],
    path: Path = DEFAULT_ROSTER_PATH,
) -> RosterPlayer:
    """
    Add a player to the roster config.

    Args:
        name: Player name
        team: Team abbreviation
        positions: List of eligible positions

    Returns:
        The added RosterPlayer with resolved NHL ID
    """
    roster = load_roster(path, resolve_ids=False)

    player = RosterPlayer(name=name, team=team, positions=positions)

    # Resolve ID
    with get_session() as session:
        try:
            player.nhl_id = resolve_player(session, name=name, team_abbrev=team)
        except Exception as e:
            print(f"Warning: Could not resolve {name}: {e}")

    roster.players.append(player)
    save_roster(roster, path)

    return player


def remove_player(name: str, path: Path = DEFAULT_ROSTER_PATH) -> bool:
    """
    Remove a player from the roster config by name.

    Returns:
        True if player was found and removed, False otherwise
    """
    roster = load_roster(path, resolve_ids=False)

    original_count = len(roster.players)
    roster.players = [p for p in roster.players if p.name.lower() != name.lower()]

    if len(roster.players) < original_count:
        save_roster(roster, path)
        return True

    return False

"""Shared fixtures for the value-layer time-leakage tests.

These tests hit the real dev database and skip cleanly if the expected
data isn't loaded. The `active_nhl_id` fixture finds a player with games
bracketing the cutoff so a decision made on the cutoff day can be checked
against games it must not see.
"""

from datetime import date

import pytest
from sqlalchemy import func

from src.core.db import get_session
from src.core.models import Game
from src.core.models.advanced_stats import GameAdvancedStats

SEASON = "20252026"
CUTOFF = date(2026, 1, 15)


def _active_nhl_id_with_games_around(cutoff: date) -> int | None:
    """Find a player with GameAdvancedStats both before and after `cutoff`."""
    with get_session() as session:
        season_prefix = int(SEASON[:4])
        game_id_min = season_prefix * 1_000_000 + 20_000
        game_id_max = season_prefix * 1_000_000 + 30_000

        # Players with games both before and on/after the cutoff
        before = (
            session.query(GameAdvancedStats.player_id)
            .join(Game, GameAdvancedStats.game_id == Game.game_id)
            .filter(
                GameAdvancedStats.situation == "all",
                GameAdvancedStats.game_id > game_id_min,
                GameAdvancedStats.game_id < game_id_max,
                Game.date < cutoff,
            )
            .group_by(GameAdvancedStats.player_id)
            .having(func.count() >= 10)
            .subquery()
        )
        row = (
            session.query(GameAdvancedStats.player_id)
            .join(Game, GameAdvancedStats.game_id == Game.game_id)
            .filter(
                GameAdvancedStats.situation == "all",
                GameAdvancedStats.player_id.in_(before),
                Game.date >= cutoff,
            )
            .group_by(GameAdvancedStats.player_id)
            .having(func.count() >= 5)
            .first()
        )
        return row[0] if row else None


@pytest.fixture(scope="module")
def active_nhl_id() -> int:
    nhl_id = _active_nhl_id_with_games_around(CUTOFF)
    if nhl_id is None:
        pytest.skip(
            f"No player with games bracketing {CUTOFF} in {SEASON} — "
            "data not loaded for this season"
        )
    return nhl_id

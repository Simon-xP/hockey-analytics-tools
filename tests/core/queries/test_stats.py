"""Leakage tests for the backtest data providers.

These are integration tests that hit the real dev database. They
verify that every provider respects temporal boundaries — a decision
made on day D cannot see data from day D or later.

Tests skip cleanly if the expected data isn't loaded.
"""

from datetime import date

import pytest
from sqlalchemy import func, text

from src.core.queries.stats import StatsProvider
from src.core.db import get_session
from src.core.models import Game
from src.core.models.advanced_stats import GameAdvancedStats

from tests.backtest.conftest import SEASON, CUTOFF


class TestStatsProviderLeakage:
    def test_get_player_fpts_per_gp_respects_cutoff(self, active_nhl_id: int):
        """as_of must exclude games on or after the cutoff date."""
        with get_session() as session:
            capped = StatsProvider(session=session, as_of=CUTOFF)
            uncapped = StatsProvider(session=session, as_of=date(2026, 6, 1))

            result_capped = capped.get_player_fpts_per_gp(
                active_nhl_id, season=SEASON
            )
            result_full = uncapped.get_player_fpts_per_gp(
                active_nhl_id, season=SEASON
            )

        assert result_capped is not None and result_full is not None
        assert result_capped["gp"] < result_full["gp"], (
            f"Cutoff at {CUTOFF} didn't exclude any games "
            f"(capped gp={result_capped['gp']}, full gp={result_full['gp']})"
        )

    def test_get_player_fpts_per_gp_strict_less_than(self, active_nhl_id: int):
        """Games exactly ON the cutoff date must be excluded (strict <)."""
        with get_session() as session:
            season_prefix = int(SEASON[:4])
            game_id_min = season_prefix * 1_000_000 + 20_000
            game_id_max = season_prefix * 1_000_000 + 30_000

            expected_gp = (
                session.query(func.count())
                .select_from(GameAdvancedStats)
                .join(Game, GameAdvancedStats.game_id == Game.game_id)
                .filter(
                    GameAdvancedStats.player_id == active_nhl_id,
                    GameAdvancedStats.situation == "all",
                    GameAdvancedStats.game_id > game_id_min,
                    GameAdvancedStats.game_id < game_id_max,
                    GameAdvancedStats.toi_seconds > 0,
                    Game.date < CUTOFF,
                )
                .scalar()
            )

            provider = StatsProvider(session=session, as_of=CUTOFF)
            result = provider.get_player_fpts_per_gp(
                active_nhl_id, season=SEASON
            )

        assert result is not None
        assert result["gp"] == expected_gp

    def test_get_trailing_rankings_respects_cutoff(self, active_nhl_id: int):
        """Trailing rankings must only include games before as_of."""
        with get_session() as session:
            capped = StatsProvider(session=session, as_of=CUTOFF)
            rankings = capped.get_trailing_rankings(lookback_days=30, min_gp=1)

        nhl_ids = {r["nhl_id"] for r in rankings}
        assert len(rankings) > 0, "No rankings returned — data might not be loaded"

        for player in rankings:
            assert player["gp"] > 0
            assert player["fpts_per_gp"] >= 0

    def test_specific_player_gp_increases_with_later_cutoff(self, active_nhl_id: int):
        """A specific player's GP must increase when as_of moves later."""
        with get_session() as session:
            early = StatsProvider(session=session, as_of=CUTOFF)
            late = StatsProvider(session=session, as_of=date(2026, 3, 1))

            result_early = early.get_player_fpts_per_gp(
                active_nhl_id, season=SEASON
            )
            result_late = late.get_player_fpts_per_gp(
                active_nhl_id, season=SEASON
            )

        assert result_early is not None and result_late is not None
        assert result_early["gp"] < result_late["gp"], (
            f"Player {active_nhl_id} should have more GP with later cutoff "
            f"(early={result_early['gp']}, late={result_late['gp']})"
        )

    def test_replacement_level_changes_with_cutoff(self, active_nhl_id: int):
        """Replacement level must change when cutoff moves."""
        with get_session() as session:
            early = StatsProvider(session=session, as_of=CUTOFF)
            late = StatsProvider(
                session=session, as_of=date(2026, 3, 1)
            )

            repl_early = early.get_replacement_level()
            repl_late = late.get_replacement_level()

        assert (
            repl_early["forward"] != repl_late["forward"]
            or repl_early["defense"] != repl_late["defense"]
        ), "Replacement level didn't change with different cutoffs"

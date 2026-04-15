"""Time-leakage tests for the transaction backtest value layer.

These tests are the keystone for the daily-decision backtest refactor.
Every call that reads historical player stats must honour an `as_of` cutoff,
and must filter strictly (`Game.date < as_of`) so a decision made on day D
cannot see games played on D itself.

The tests hit the real dev database — they're integration-level and will
skip cleanly if the expected data isn't loaded.
"""

from datetime import date

import pytest
from sqlalchemy import func

from src.api.stats_helpers import compute_fpts_per_gp
from src.core.db import get_session
from src.core.models import Game
from src.core.models.advanced_stats import GameAdvancedStats
from src.tools.transactions.player_value import compute_player_value_simple
from src.tools.transactions.replacement_level import compute_replacement_level


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


class TestComputeFptsPerGpLeakage:
    def test_as_of_shrinks_games_played(self, active_nhl_id: int):
        """With `as_of` set, `gp` must be strictly smaller than without it."""
        with get_session() as session:
            full = compute_fpts_per_gp(session, active_nhl_id, SEASON)
            capped = compute_fpts_per_gp(
                session, active_nhl_id, SEASON, as_of=CUTOFF
            )

        assert full is not None and capped is not None
        assert capped["gp"] < full["gp"], (
            f"as_of={CUTOFF} didn't exclude any games "
            f"(full gp={full['gp']}, capped gp={capped['gp']})"
        )

    def test_no_games_on_or_after_cutoff(self, active_nhl_id: int):
        """The games feeding `compute_fpts_per_gp(as_of=X)` must all satisfy
        `Game.date < X` — strict less-than is the decision-deadline guarantee.
        """
        with get_session() as session:
            capped = compute_fpts_per_gp(
                session, active_nhl_id, SEASON, as_of=CUTOFF
            )
            assert capped is not None

            # Independently recount the games that should have been included
            # and assert none of them violate the cutoff.
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

            assert capped["gp"] == expected_gp

            # And confirm the filter is strictly exclusive of `CUTOFF`.
            leaked = (
                session.query(func.count())
                .select_from(GameAdvancedStats)
                .join(Game, GameAdvancedStats.game_id == Game.game_id)
                .filter(
                    GameAdvancedStats.player_id == active_nhl_id,
                    GameAdvancedStats.situation == "all",
                    GameAdvancedStats.game_id > game_id_min,
                    GameAdvancedStats.game_id < game_id_max,
                    Game.date >= CUTOFF,
                )
                .scalar()
            )
            # There must be some games at/after the cutoff for the test
            # to be meaningful — otherwise we'd pass trivially.
            assert leaked > 0, "Test player has no post-cutoff games — pick another"


class TestPlayerValueSimpleLeakage:
    def test_as_of_propagates_to_compute_player_value_simple(self, active_nhl_id: int):
        """`compute_player_value_simple(as_of=X)` must not reflect post-X stats."""
        with get_session() as session:
            full = compute_player_value_simple(session, active_nhl_id, yahoo_week=10)
            capped = compute_player_value_simple(
                session, active_nhl_id, yahoo_week=10, as_of=CUTOFF
            )

        assert full is not None and capped is not None
        # The capped version saw fewer games, so its GP count must be lower.
        assert capped.games_played < full.games_played


class TestReplacementLevelLeakage:
    def test_as_of_flows_through_replacement_level(self, active_nhl_id: int):
        """`compute_replacement_level(as_of=X)` must cap the underlying stats."""
        with get_session() as session:
            # Minimal FA dict shaped like the Yahoo API payload.
            from src.core.models import Player
            p = session.query(Player).filter(Player.nhl_id == active_nhl_id).first()
            assert p is not None
            fa_dicts = [{
                "name": p.full_name,
                "team": p.team.abbrev if p.team else None,
                "position": p.position or "C",
            }]

            repl_full = compute_replacement_level(
                session, fa_dicts, top_n=1, min_gp=1,
            )
            repl_capped = compute_replacement_level(
                session, fa_dicts, top_n=1, min_gp=1, as_of=CUTOFF,
            )

        # We don't know which direction the number moves — we just need to
        # confirm the cutoff actually *changes* the computed value. A stable
        # value would suggest `as_of` isn't being threaded through.
        assert repl_full.forward != repl_capped.forward \
            or repl_full.defense != repl_capped.defense

"""Time-leakage tests for `src.core.queries.stats_helpers.compute_fpts_per_gp`.

Every call that reads historical player stats must honour an `as_of` cutoff,
and must filter strictly (`Game.date < as_of`) so a decision made on day D
cannot see games played on D itself.

The tests hit the real dev database — they're integration-level and will
skip cleanly if the expected data isn't loaded.
"""

from sqlalchemy import func

from src.core.queries.stats_helpers import compute_fpts_per_gp
from src.core.db import get_session
from src.core.models import Game
from src.core.models.advanced_stats import GameAdvancedStats

from tests.conftest import SEASON, CUTOFF


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

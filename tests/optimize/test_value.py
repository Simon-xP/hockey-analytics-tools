"""Time-leakage tests for `src.optimize.value`.

`compute_player_value_simple(as_of=X)` must not reflect stats from games
played on or after X. Integration-level against the real dev database;
skips cleanly if the expected data isn't loaded.
"""

from src.core.db import get_session
from src.optimize.value import compute_player_value_simple

from tests.conftest import CUTOFF


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

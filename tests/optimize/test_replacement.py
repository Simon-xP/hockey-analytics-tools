"""Time-leakage tests for `src.optimize.replacement`.

`compute_replacement_level(as_of=X)` must cap the underlying stats at the
cutoff. Integration-level against the real dev database; skips cleanly if
the expected data isn't loaded.
"""

from src.core.db import get_session
from src.optimize.replacement import compute_replacement_level

from tests.conftest import CUTOFF


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

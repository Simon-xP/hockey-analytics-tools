"""Acceptance scenarios for the lineup grid.

These are written to be read. Each one is a situation a fantasy manager would
recognize, and the assertion says what the right answer is in those terms.
"""

from datetime import date

import pytest

from src.optimize.models import PlayerType, RosterPlayer, RosterSlotSettings
from src.optimize.models.week import Candidate
from src.optimize.slots import assign_players_to_slots
from src.optimize.week.lineup import build_grid, terminal_value, window_projection
from tests.optimize.week.conftest import (
    MONDAY,
    SUNDAY,
    THURSDAY,
    TUESDAY,
    WEDNESDAY,
    entry,
    make_state,
)


def candidate(
    nhl_id: int,
    name: str,
    positions: tuple[str, ...],
    projections: dict[date, float],
    *,
    team_abbrev: str = "BOS",
    available_from: date | None = None,
    player_type: PlayerType = PlayerType.SKATER,
) -> Candidate:
    return Candidate(
        nhl_id=nhl_id,
        name=name,
        team_abbrev=team_abbrev,
        positions=positions,
        player_type=player_type,
        projections=projections,
        terminal_value=0.0,
        available_from=available_from,
        upside_score=0.0,
        opportunity_score=0.0,
        confidence=0.8,
        source="fa",
    )


# ---------------------------------------------------------------------------
# Grid correctness
# ---------------------------------------------------------------------------


class TestGridCorrectness:
    def test_slot_blocked_player_scores_zero(self):
        """Three healthy centers, two C slots, no UTIL: the worst one sits."""
        roster = (
            entry(1, "Best C", ("C",)),
            entry(2, "Middle C", ("C",)),
            entry(3, "Worst C", ("C",)),
        )
        slots = RosterSlotSettings(c=2, lw=0, rw=0, d=0, g=0, util=0, bn=3, ir=0, ir_plus=0)
        state = make_state(
            roster,
            {(1, MONDAY): 6.0, (2, MONDAY): 4.0, (3, MONDAY): 2.0},
            slots=slots,
        )

        grid = build_grid(state, MONDAY, MONDAY)
        day = grid.days[0]

        assert day.starters == (1, 2)
        assert day.benched == (3,)
        assert grid.mu() == pytest.approx(10.0)

    def test_multi_position_player_fills_the_scarce_slot(self):
        """A C/LW starts at LW when the C slot is already spoken for."""
        roster = (
            entry(1, "Pure C", ("C",)),
            entry(2, "Swing", ("C", "LW")),
        )
        slots = RosterSlotSettings(c=1, lw=1, rw=0, d=0, g=0, util=0, bn=3, ir=0, ir_plus=0)
        state = make_state(
            roster, {(1, MONDAY): 5.0, (2, MONDAY): 4.0}, slots=slots
        )

        grid = build_grid(state, MONDAY, MONDAY)

        assert set(grid.days[0].starters) == {1, 2}
        assert grid.days[0].benched == ()
        assert grid.mu() == pytest.approx(9.0)

    def test_util_absorbs_the_overflow(self):
        """With every named slot full, UTIL takes the best leftover, not any leftover."""
        roster = (
            entry(1, "Starter C", ("C",)),
            entry(2, "Good leftover", ("C",)),
            entry(3, "Poor leftover", ("C",)),
        )
        slots = RosterSlotSettings(c=1, lw=0, rw=0, d=0, g=0, util=1, bn=3, ir=0, ir_plus=0)
        state = make_state(
            roster,
            {(1, MONDAY): 7.0, (2, MONDAY): 5.0, (3, MONDAY): 1.0},
            slots=slots,
        )

        grid = build_grid(state, MONDAY, MONDAY)

        assert set(grid.days[0].starters) == {1, 2}
        assert grid.days[0].benched == (3,)

    def test_assignment_beats_greedy(self):
        """The greedy assigner benches a 9-FPTS star here. The weighted one must not.

        Two C slots, one LW slot, four players. Greedy goes single-position
        first, so both C slots go to 1-FPTS centers and the lone LW slot to a
        1-FPTS winger. The 9-FPTS C/LW arrives last, finds nothing open, and
        sits: 3.0 points. The optimum starts the star at C and leaves a cheap
        center on the bench instead: 11.0 points.
        """
        players = [
            RosterPlayer(name="Cheap C1", team="TOR", positions=["C"], nhl_id=1),
            RosterPlayer(name="Cheap C2", team="TOR", positions=["C"], nhl_id=2),
            RosterPlayer(name="Star C/LW", team="TOR", positions=["C", "LW"], nhl_id=3),
            RosterPlayer(name="Cheap LW", team="TOR", positions=["LW"], nhl_id=4),
        ]
        projections = {1: 1.0, 2: 1.0, 3: 9.0, 4: 1.0}
        slots = RosterSlotSettings(c=2, lw=1, rw=0, d=0, g=0, util=0, bn=3, ir=0, ir_plus=0)

        def total(assignments):
            return sum(
                projections[p.nhl_id]
                for assigned in assignments.values()
                for p in assigned
            )

        greedy = assign_players_to_slots(players, slots)
        weighted = assign_players_to_slots(players, slots, projections=projections)

        assert total(weighted) > total(greedy)
        assert total(weighted) == pytest.approx(11.0)

    def test_duplicate_names_do_not_collide(self):
        """Two Sebastian Ahos, different NHL IDs, both make the lineup."""
        roster = (
            entry(8480222, "Sebastian Aho", ("D",), team_abbrev="NYI"),
            entry(8478427, "Sebastian Aho", ("C",), team_abbrev="CAR"),
        )
        slots = RosterSlotSettings(c=1, lw=0, rw=0, d=1, g=0, util=0, bn=3, ir=0, ir_plus=0)
        state = make_state(
            roster,
            {(8480222, MONDAY): 3.0, (8478427, MONDAY): 6.0},
            slots=slots,
        )

        grid = build_grid(state, MONDAY, MONDAY)

        assert set(grid.days[0].starters) == {8480222, 8478427}
        assert grid.mu() == pytest.approx(9.0)

    def test_idle_team_contributes_nothing_and_frees_no_slot(self):
        """A player whose team is off has no projection, so the slot reads open."""
        roster = (entry(1, "Only C", ("C",)),)
        slots = RosterSlotSettings(c=2, lw=0, rw=0, d=0, g=0, util=0, bn=3, ir=0, ir_plus=0)
        state = make_state(roster, {(1, MONDAY): 4.0}, slots=slots)

        grid = build_grid(state, MONDAY, TUESDAY)

        assert grid.days[0].open_slots["C"] == 1  # one C slot still free Monday
        assert grid.days[1].mu == 0.0
        assert grid.days[1].open_slots["C"] == 2  # nobody plays Tuesday


# ---------------------------------------------------------------------------
# with_move
# ---------------------------------------------------------------------------


class TestWithMove:
    def _base(self):
        roster = (
            entry(1, "Rostered C", ("C",)),
            entry(2, "Droppable C", ("C",)),
        )
        slots = RosterSlotSettings(c=2, lw=0, rw=0, d=0, g=0, util=0, bn=3, ir=0, ir_plus=0)
        projections = {
            (1, MONDAY): 4.0, (1, WEDNESDAY): 4.0, (1, THURSDAY): 4.0,
            (2, MONDAY): 1.0, (2, WEDNESDAY): 1.0, (2, THURSDAY): 1.0,
        }
        return make_state(roster, projections, slots=slots)

    def test_move_applies_same_day(self):
        """Yahoo processes adds immediately: a Wednesday add plays Wednesday."""
        state = self._base()
        grid = build_grid(state, MONDAY, THURSDAY)
        pickup = candidate(
            9, "Hot streamer", ("C",),
            {WEDNESDAY: 8.0, THURSDAY: 8.0},
        )

        after = grid.with_move(add=pickup, drop=2, on_date=WEDNESDAY)

        assert grid.day(WEDNESDAY).mu == pytest.approx(5.0)
        assert after.day(WEDNESDAY).mu == pytest.approx(12.0)
        assert 9 in after.day(WEDNESDAY).starters

    def test_move_does_not_touch_the_past(self):
        """Days before the fire date are the same objects, not just equal ones."""
        state = self._base()
        grid = build_grid(state, MONDAY, THURSDAY)
        pickup = candidate(9, "Hot streamer", ("C",), {WEDNESDAY: 8.0, THURSDAY: 8.0})

        after = grid.with_move(add=pickup, drop=2, on_date=WEDNESDAY)

        assert after.day(MONDAY) is grid.day(MONDAY)
        assert after.day(TUESDAY) is grid.day(TUESDAY)
        assert after.day(MONDAY).mu == pytest.approx(5.0)

    def test_free_add_needs_no_drop(self):
        """`drop=None` against an open slot is legal and raises the projection."""
        state = self._base()
        grid = build_grid(state, MONDAY, THURSDAY)
        pickup = candidate(9, "Free pickup", ("C",), {THURSDAY: 6.0})

        after = grid.with_move(add=pickup, drop=None, on_date=THURSDAY)

        # Two C slots, three centers playing Thursday: the 1.0 guy is now the
        # odd one out, so the gain is 6.0 - 1.0.
        assert after.mu() - grid.mu() == pytest.approx(5.0)

    def test_pure_drop_is_legal(self):
        """`add=None` removes a player and nothing else."""
        state = self._base()
        grid = build_grid(state, MONDAY, THURSDAY)

        after = grid.with_move(add=None, drop=2, on_date=WEDNESDAY)

        assert after.day(MONDAY).mu == pytest.approx(5.0)
        assert after.day(WEDNESDAY).mu == pytest.approx(4.0)

    def test_chained_move(self):
        """Add A on Monday, then drop A for B on Thursday."""
        state = self._base()
        grid = build_grid(state, MONDAY, THURSDAY)
        player_a = candidate(
            9, "Monday pickup", ("C",),
            {MONDAY: 5.0, WEDNESDAY: 5.0, THURSDAY: 5.0},
        )
        player_b = candidate(10, "Thursday pickup", ("C",), {THURSDAY: 7.0})

        chained = (
            grid.with_move(add=player_a, drop=2, on_date=MONDAY)
                .with_move(add=player_b, drop=9, on_date=THURSDAY)
        )

        assert 9 in chained.roster_ids_on(MONDAY)
        assert 9 in chained.roster_ids_on(WEDNESDAY)
        assert 9 not in chained.roster_ids_on(THURSDAY)
        assert 10 in chained.roster_ids_on(THURSDAY)
        assert chained.day(MONDAY).mu == pytest.approx(9.0)   # 4.0 + 5.0
        assert chained.day(THURSDAY).mu == pytest.approx(11.0)  # 4.0 + 7.0

    def test_move_order_does_not_matter(self):
        """Applying Thursday's move first still reads correctly on Monday."""
        state = self._base()
        grid = build_grid(state, MONDAY, THURSDAY)
        player_a = candidate(9, "Monday pickup", ("C",), {MONDAY: 5.0, WEDNESDAY: 5.0})
        player_b = candidate(10, "Thursday pickup", ("C",), {THURSDAY: 7.0})

        forwards = (
            grid.with_move(add=player_a, drop=2, on_date=MONDAY)
                .with_move(add=player_b, drop=9, on_date=THURSDAY)
        )
        backwards = (
            grid.with_move(add=player_b, drop=9, on_date=THURSDAY)
                .with_move(add=player_a, drop=2, on_date=MONDAY)
        )

        assert forwards.mu() == pytest.approx(backwards.mu())

    def test_waiver_claim_contributes_nothing_before_it_clears(self):
        """`available_from` gates the projection, whatever date the plan fires."""
        state = self._base()
        grid = build_grid(state, MONDAY, THURSDAY)
        claim = candidate(
            9, "On waivers", ("C",),
            {WEDNESDAY: 8.0, THURSDAY: 8.0},
            available_from=THURSDAY,
        )

        after = grid.with_move(add=claim, drop=2, on_date=WEDNESDAY)

        assert after.day(WEDNESDAY).mu == pytest.approx(4.0)  # claim not in yet
        assert after.day(THURSDAY).mu == pytest.approx(12.0)

    def test_grid_is_not_mutated_by_a_move(self):
        state = self._base()
        grid = build_grid(state, MONDAY, THURSDAY)
        before = grid.mu()

        grid.with_move(add=candidate(9, "X", ("C",), {MONDAY: 9.0}), drop=2, on_date=MONDAY)

        assert grid.mu() == pytest.approx(before)
        assert grid.moves == ()

    def test_marginal_matches_a_full_recompute(self):
        state = self._base()
        grid = build_grid(state, MONDAY, THURSDAY)
        pickup = candidate(9, "Free pickup", ("C",), {THURSDAY: 6.0})

        delta_mu, delta_var = grid.marginal(pickup, THURSDAY)
        after = grid.with_move(add=pickup, drop=None, on_date=THURSDAY)

        assert delta_mu == pytest.approx(after.mu() - grid.mu())
        assert delta_var == pytest.approx(
            sum(d.var for d in after.days) - sum(d.var for d in grid.days)
        )


# ---------------------------------------------------------------------------
# Terminal value
# ---------------------------------------------------------------------------


class TestTerminalValue:
    def test_sums_the_seven_days_after_the_window(self):
        from datetime import timedelta

        roster = (entry(1, "C", ("C",)),)
        projections = {
            (1, SUNDAY): 5.0,                        # inside the window
            (1, SUNDAY + timedelta(days=1)): 3.0,    # terminal
            (1, SUNDAY + timedelta(days=4)): 4.0,    # terminal
            (1, SUNDAY + timedelta(days=9)): 9.0,    # beyond terminal
        }
        state = make_state(roster, projections)

        assert terminal_value(state, 1, SUNDAY) == pytest.approx(7.0)

    def test_window_projection_omits_days_without_a_game(self):
        roster = (entry(1, "C", ("C",)),)
        state = make_state(roster, {(1, MONDAY): 4.0, (1, THURSDAY): 3.0})

        projections = window_projection(state, 1, MONDAY, THURSDAY)

        assert projections == {MONDAY: 4.0, THURSDAY: 3.0}
        assert TUESDAY not in projections

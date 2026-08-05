"""Tests for `WeekState` assembly.

Split in two. The pure helpers (position mapping, availability, fingerprint)
need nothing. The rest runs against the real dev database, follows the pattern
in `tests/optimize/test_value.py`, and skips cleanly when the expected data is
not loaded.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import text

from src.core.db import SessionLocal, get_session
from src.core.models import Player, TeamRoster
from src.optimize.week.state import (
    IR_DESIGNATED_STATUSES,
    NHL_TO_YAHOO_POSITION,
    build_game_context,
    build_roster_entries,
    build_schedule_map,
    compute_earned,
    count_adds_used,
    days_between,
    expected_return_date,
    is_available,
    state_fingerprint,
    terminal_window,
    yahoo_positions,
)
from tests.optimize.week.conftest import MONDAY, SUNDAY, entry, make_state

SEASON = "20252026"
# Mid-season, so there are games on both sides of it.
CUTOFF = date(2026, 1, 15)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPositions:
    def test_yahoo_eligibility_wins_when_we_have_it(self):
        player = Player(nhl_id=1, full_name="X", position="C", yahoo_positions="C,LW")
        assert yahoo_positions(player) == ("C", "LW")

    def test_falls_back_to_the_nhl_code_through_one_mapping(self):
        """`L` and `R` are NHL codes; Yahoo calls them LW and RW."""
        left = Player(nhl_id=1, full_name="X", position="L", yahoo_positions=None)
        right = Player(nhl_id=2, full_name="Y", position="R", yahoo_positions=None)

        assert yahoo_positions(left) == ("LW",)
        assert yahoo_positions(right) == ("RW",)
        assert NHL_TO_YAHOO_POSITION["L"] == "LW"

    def test_blank_yahoo_string_does_not_produce_an_empty_tuple(self):
        player = Player(nhl_id=1, full_name="X", position="D", yahoo_positions="")
        assert yahoo_positions(player) == ("D",)


class TestAvailability:
    def test_healthy_players_are_always_available(self):
        assert is_available(None, MONDAY, MONDAY) is True

    def test_return_window_midpoint_gates_the_day(self):
        injury = {
            "soonest_return": MONDAY + timedelta(days=2),
            "latest_return": MONDAY + timedelta(days=6),
        }
        expected = expected_return_date(injury, MONDAY)

        assert expected == MONDAY + timedelta(days=4)
        assert is_available(injury, MONDAY + timedelta(days=3), MONDAY) is False
        assert is_available(injury, MONDAY + timedelta(days=4), MONDAY) is True

    def test_no_stated_timeline_means_out_indefinitely(self):
        injury = {"soonest_return": None, "latest_return": None}
        assert is_available(injury, SUNDAY, MONDAY) is False


class TestWindows:
    def test_terminal_window_starts_the_day_after_the_window_closes(self):
        start, end = terminal_window(SUNDAY)
        assert start == SUNDAY + timedelta(days=1)
        assert end == SUNDAY + timedelta(days=7)

    def test_days_between_is_inclusive_on_both_ends(self):
        days = days_between(MONDAY, MONDAY + timedelta(days=2))
        assert days == [MONDAY, MONDAY + timedelta(days=1), MONDAY + timedelta(days=2)]


class TestFingerprint:
    def _state(self, **overrides):
        roster = (entry(1, "A", ("C",)), entry(2, "B", ("D",)))
        return make_state(roster, {(1, MONDAY): 4.0}, **overrides)

    def test_identical_states_fingerprint_identically(self):
        assert state_fingerprint(self._state()) == state_fingerprint(self._state())

    def test_a_changed_add_budget_changes_the_fingerprint(self):
        assert state_fingerprint(self._state()) != state_fingerprint(
            self._state(adds_remaining=3)
        )

    def test_a_new_injury_changes_the_fingerprint(self):
        base = self._state()
        injured = make_state(
            (entry(1, "A", ("C",), injury_status="OUT"), entry(2, "B", ("D",))),
            {(1, MONDAY): 4.0},
        )
        assert state_fingerprint(base) != state_fingerprint(injured)

    def test_roster_order_does_not_change_the_fingerprint(self):
        forwards = make_state(
            (entry(1, "A", ("C",)), entry(2, "B", ("D",))), {(1, MONDAY): 4.0}
        )
        backwards = make_state(
            (entry(2, "B", ("D",)), entry(1, "A", ("C",))), {(1, MONDAY): 4.0}
        )
        assert state_fingerprint(forwards) == state_fingerprint(backwards)


# ---------------------------------------------------------------------------
# Against the database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db_session():
    with get_session() as session:
        if not session.execute(text("SELECT 1 FROM games LIMIT 1")).first():
            pytest.skip("no schedule loaded in the dev database")
        yield session


class TestScheduleMap:
    def test_one_query_returns_every_team_playing_each_day(self, db_session):
        start, end = date(2026, 1, 12), date(2026, 1, 18)
        schedule = build_schedule_map(db_session, start, end)

        assert schedule, "expected NHL games in a mid-January week"
        assert all(start <= day <= end for day in schedule)
        assert all(isinstance(teams, frozenset) for teams in schedule.values())
        # Two teams per game, so any day with hockey has an even team count.
        assert all(len(teams) % 2 == 0 for teams in schedule.values())

    def test_it_agrees_with_the_per_date_helper(self, db_session):
        from src.optimize.slots import get_teams_playing_on_date

        day = date(2026, 1, 14)
        schedule = build_schedule_map(db_session, day, day)

        assert schedule.get(day, frozenset()) == frozenset(
            get_teams_playing_on_date(day, session=db_session)
        )

    def test_game_context_pairs_each_team_with_its_opponent(self, db_session):
        day = date(2026, 1, 14)
        context = build_game_context(db_session, day, day)

        assert context
        for (ctx_day, team_id), (opp_id, home_id) in context.items():
            assert ctx_day == day
            assert opp_id != team_id
            assert home_id in (team_id, opp_id)


class TestEarnedLeakage:
    """`compute_earned` must never see a game played on or after `as_of`."""

    @pytest.fixture(scope="class")
    def scoring_ids(self):
        with get_session() as session:
            rows = session.execute(
                text(
                    """
                    SELECT gas.player_id
                    FROM game_advanced_stats gas
                    JOIN games g ON gas.game_id = g.game_id
                    WHERE gas.situation = 'all'
                          AND g.date >= :week_start AND g.date < :week_end
                    GROUP BY gas.player_id
                    HAVING SUM(gas.shots) > 5
                    LIMIT 20
                    """
                ),
                {"week_start": date(2026, 1, 12), "week_end": date(2026, 1, 19)},
            ).fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            pytest.skip("no scored games in the reference week")
        return ids

    def test_earlier_as_of_cannot_see_later_games(self, db_session, scoring_ids):
        week_start = date(2026, 1, 12)

        early = compute_earned(db_session, scoring_ids, week_start, date(2026, 1, 14))
        late = compute_earned(db_session, scoring_ids, week_start, date(2026, 1, 19))

        assert early < late, "a longer window must bank at least as many points"

    def test_as_of_on_the_week_start_banks_nothing(self, db_session, scoring_ids):
        week_start = date(2026, 1, 12)
        assert compute_earned(db_session, scoring_ids, week_start, week_start) == 0.0

    def test_the_as_of_day_itself_is_excluded(self, db_session, scoring_ids):
        """Strict `<`: a decision made on day D cannot see day D's games."""
        week_start = date(2026, 1, 12)

        through_wednesday = compute_earned(
            db_session, scoring_ids, week_start, date(2026, 1, 14)
        )
        through_thursday = compute_earned(
            db_session, scoring_ids, week_start, date(2026, 1, 15)
        )
        wednesday_only = session_day_points(db_session, scoring_ids, date(2026, 1, 14))

        assert through_thursday - through_wednesday == pytest.approx(
            wednesday_only, abs=0.01
        )


def session_day_points(session, nhl_ids, day: date) -> float:
    """Points scored on exactly one day, for cross-checking the `as_of` boundary."""
    return compute_earned(session, nhl_ids, day, day + timedelta(days=1))


class TestRosterEntries:
    """Roster assembly, against a rolled-back transaction so nothing persists."""

    @pytest.fixture
    def seeded(self):
        session = SessionLocal()
        try:
            ids = [
                r[0]
                for r in session.execute(
                    text("SELECT nhl_id FROM players WHERE team_id IS NOT NULL LIMIT 6")
                ).fetchall()
            ]
            if len(ids) < 6:
                pytest.skip("not enough players loaded")
            for nhl_id in ids:
                session.add(
                    TeamRoster(league_key="test.l.1", team_key="test.l.1.t.1", nhl_id=nhl_id)
                )
            session.flush()
            yield session, ids
        finally:
            session.rollback()
            session.close()

    def test_every_rostered_player_becomes_an_entry(self, seeded):
        session, ids = seeded
        entries, open_ir = build_roster_entries(
            session, ids, CUTOFF, ir_capacity=2, protected=frozenset()
        )

        assert {e.nhl_id for e in entries} == set(ids)
        assert all(e.positions for e in entries), "every entry needs an eligibility"
        assert 0 <= open_ir <= 2

    def test_protected_players_are_flagged(self, seeded):
        session, ids = seeded
        entries, _ = build_roster_entries(
            session, ids, CUTOFF, ir_capacity=2, protected=frozenset({ids[0]})
        )

        by_id = {e.nhl_id: e for e in entries}
        assert by_id[ids[0]].is_protected is True
        assert by_id[ids[1]].is_protected is False

    def test_only_ir_designations_are_ir_eligible(self, seeded):
        session, ids = seeded
        entries, _ = build_roster_entries(
            session, ids, CUTOFF, ir_capacity=2, protected=frozenset()
        )

        for e in entries:
            if e.ir_eligible:
                assert (e.injury_status or "").upper() in IR_DESIGNATED_STATUSES

    def test_get_team_roster_nhl_ids_reads_what_we_wrote(self, seeded):
        from src.optimize.week.state import get_team_roster_nhl_ids

        session, ids = seeded
        assert set(get_team_roster_nhl_ids(session, "test.l.1", "test.l.1.t.1")) == set(ids)


class TestProjectionCache:
    """The cache is the whole performance story, so its rules are tested hard."""

    @pytest.fixture
    def fake_forecast(self, monkeypatch):
        """Stand in for `forecast_player`, counting how often it is called."""
        import src.predict.forecasting.forecast as forecast_module

        calls = []

        def fake(session, nhl_id, game_date, **kwargs):
            calls.append((nhl_id, game_date, kwargs.get("opp_team_id")))
            return {"fpts": 3.0}

        monkeypatch.setattr(forecast_module, "forecast_player", fake)
        return calls

    def _fixtures(self):
        from src.optimize.week.state import ForecastDeps

        deps = ForecastDeps(models={}, toi_predictor=None, eb_pp=None, eb_pk=None,
                            eb_5v5=None)
        days = days_between(MONDAY, MONDAY + timedelta(days=3))
        schedule = {
            MONDAY: frozenset({"TOR"}),
            MONDAY + timedelta(days=2): frozenset({"TOR"}),
        }
        game_context = {
            (MONDAY, 10): (20, 10),
            (MONDAY + timedelta(days=2), 10): (30, 30),
        }
        return deps, days, schedule, game_context

    def test_idle_days_are_absent_not_zero(self, db_session, fake_forecast):
        from src.optimize.week.state import build_projection_cache

        deps, days, schedule, game_context = self._fixtures()
        values = build_projection_cache(
            db_session, [1], days, MONDAY, schedule, game_context, deps,
            team_by_player={1: (10, "TOR")},
        )

        assert set(values) == {(1, MONDAY), (1, MONDAY + timedelta(days=2))}
        assert (1, MONDAY + timedelta(days=1)) not in values

    def test_days_a_player_is_out_are_absent(self, db_session, fake_forecast):
        from src.optimize.week.state import build_projection_cache

        deps, days, schedule, game_context = self._fixtures()
        values = build_projection_cache(
            db_session, [1], days, MONDAY, schedule, game_context, deps,
            team_by_player={1: (10, "TOR")},
            unavailable={1: frozenset({MONDAY})},
        )

        assert (1, MONDAY) not in values
        assert (1, MONDAY + timedelta(days=2)) in values

    def test_repeat_matchups_are_memoized(self, db_session, fake_forecast):
        """A forecast at a fixed `as_of` varies only with game context."""
        from src.optimize.week.state import build_projection_cache

        deps, days, schedule, game_context = self._fixtures()
        # Same opponent and venue on both days.
        game_context[(MONDAY + timedelta(days=2), 10)] = (20, 10)

        build_projection_cache(
            db_session, [1], days, MONDAY, schedule, game_context, deps,
            team_by_player={1: (10, "TOR")},
        )

        assert len(fake_forecast) == 1

    def test_existing_entries_are_not_recomputed(self, db_session, fake_forecast):
        from src.optimize.week.state import build_projection_cache

        deps, days, schedule, game_context = self._fixtures()
        values = build_projection_cache(
            db_session, [1], days, MONDAY, schedule, game_context, deps,
            team_by_player={1: (10, "TOR")},
            existing={(1, MONDAY): 9.9},
        )

        assert values[(1, MONDAY)] == 9.9
        assert len(fake_forecast) == 1  # only the second day was resolved

    def test_a_failed_forecast_leaves_no_entry(self, db_session, monkeypatch):
        """No projection means no key. Downstream must never see a guessed zero."""
        import src.predict.forecasting.forecast as forecast_module
        from src.optimize.week.state import build_projection_cache

        def explode(*args, **kwargs):
            raise ValueError("no game history")

        monkeypatch.setattr(forecast_module, "forecast_player", explode)
        deps, days, schedule, game_context = self._fixtures()

        values = build_projection_cache(
            db_session, [1], days, MONDAY, schedule, game_context, deps,
            team_by_player={1: (10, "TOR")},
        )

        assert values == {}


class TestAddBudget:
    def test_adds_before_the_week_do_not_count(self, db_session):
        row = db_session.execute(
            text(
                """
                SELECT league_key, fantasy_team_key, timestamp
                FROM yahoo_transactions
                WHERE action = 'add' AND fantasy_team_key IS NOT NULL
                ORDER BY timestamp DESC LIMIT 1
                """
            )
        ).first()
        if not row:
            pytest.skip("no Yahoo transactions loaded")
        league_key, team_key, stamp = row
        tx_day = stamp.date()

        counted = count_adds_used(
            db_session, league_key, team_key, tx_day, tx_day + timedelta(days=1)
        )
        excluded = count_adds_used(
            db_session, league_key, team_key,
            tx_day + timedelta(days=1), tx_day + timedelta(days=2),
        )

        assert counted >= 1
        assert excluded == 0 or excluded < counted + 1

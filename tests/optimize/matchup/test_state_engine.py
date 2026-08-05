"""Tests for the matchup state engine.

The calibration table lives in `test_posture_scenarios.py`. This file covers the
composition layer: the `determine_aggression` shim that legacy callers still
use, and `auto_importance`.
"""

from datetime import date

from src.optimize.matchup.state_engine import (
    auto_importance,
    determine_aggression,
    determine_posture,
)
from src.optimize.models import (
    AggressionLevel,
    MatchupContext,
    MatchupSnapshot,
    PickupBoost,
    TeamProjection,
    WeekImportance,
)
from src.optimize.models.week import PostureMode

MONDAY = date(2026, 1, 5)
SUNDAY = date(2026, 1, 11)


def _snapshot(**overrides):
    defaults = dict(
        my_team_key="t1",
        opp_team_key="t2",
        my_earned=0.0,
        opp_earned=0.0,
        week_start=MONDAY,
        week_end=SUNDAY,
        my_adds_remaining=4,
        opp_adds_remaining=4,
        yahoo_week=10,
    )
    defaults.update(overrides)
    return MatchupSnapshot(**defaults)


def _projection(team_key="t1", earned=0.0, mu=0.0, sigma=0.0, games=0, fillable=0):
    return TeamProjection(
        team_key=team_key,
        earned=earned,
        mu_remaining=mu,
        sigma_remaining=sigma,
        remaining_games=games,
        remaining_fillable_games=fillable,
    )


def _no_boost():
    return PickupBoost(mu_boost=0.0, sigma_boost=0.0, n_adds_remaining=0)


def _ctx(
    my_earned=0.0,
    opp_earned=0.0,
    my_mu=0.0,
    opp_mu=0.0,
    my_sigma=0.0,
    opp_sigma=0.0,
    opp_boost_mu=0.0,
    opp_boost_sigma=0.0,
    importance=WeekImportance.BIG,
    my_rank=8,
):
    return MatchupContext(
        snapshot=_snapshot(my_earned=my_earned, opp_earned=opp_earned),
        my_projection=_projection("t1", my_earned, my_mu, my_sigma),
        opp_projection=_projection("t2", opp_earned, opp_mu, opp_sigma),
        my_pickup_boost=_no_boost(),
        opp_pickup_boost=PickupBoost(opp_boost_mu, opp_boost_sigma, 4 if opp_boost_mu else 0),
        importance=importance,
        my_rank=my_rank,
    )


class TestDeterminePosture:
    def test_behind_high_variance_aggressive(self):
        """Behind but high variance (many games left) -> contest, AGGRESSIVE."""
        ctx = _ctx(my_earned=50, opp_earned=70, my_sigma=30, opp_sigma=30)
        posture = determine_posture(ctx, MONDAY)
        assert posture.mode == PostureMode.CONTEST
        assert posture.aggression == AggressionLevel.AGGRESSIVE

    def test_lost_cause_big_week_punts(self):
        """Lost cause in a BIG week -> PUNT (was PREPARE)."""
        ctx = _ctx(my_earned=50, opp_earned=100, my_sigma=3, opp_sigma=3)
        posture = determine_posture(ctx, MONDAY)
        assert posture.mode == PostureMode.PUNT
        assert posture.p_win < 0.02

    def test_lost_cause_crazy_week_desperate(self):
        """Lost cause in a CRAZY week -> contest, DESPERATE. Playoffs never concede."""
        ctx = _ctx(
            my_earned=50, opp_earned=100, my_sigma=3, opp_sigma=3, importance=WeekImportance.CRAZY
        )
        posture = determine_posture(ctx, MONDAY)
        assert posture.mode == PostureMode.CONTEST
        assert posture.aggression == AggressionLevel.DESPERATE

    def test_lost_cause_neutral_week_punts(self):
        """Lost cause in a NEUTRAL week -> PUNT, and earlier than BIG would."""
        ctx = _ctx(
            my_earned=50, opp_earned=100, my_sigma=3, opp_sigma=3, importance=WeekImportance.NEUTRAL
        )
        assert determine_posture(ctx, MONDAY).mode == PostureMode.PUNT

    def test_large_lead_conservative(self):
        """Solid lead with variance -> contest, CONSERVATIVE."""
        ctx = _ctx(my_earned=120, opp_earned=100, my_sigma=10, opp_sigma=10)
        posture = determine_posture(ctx, MONDAY)
        assert posture.mode == PostureMode.CONTEST
        assert posture.aggression == AggressionLevel.CONSERVATIVE

    def test_very_large_lead_punts(self):
        """Huge lead -> PUNT, and the window slides to next week."""
        ctx = _ctx(my_earned=180, opp_earned=100, my_sigma=5, opp_sigma=5)
        posture = determine_posture(ctx, MONDAY)
        assert posture.mode == PostureMode.PUNT
        assert posture.p_win > 0.98
        assert posture.window_start == date(2026, 1, 12)

    def test_very_large_lead_crazy_conservative(self):
        """Huge lead in a CRAZY week -> contest, CONSERVATIVE. Protect it."""
        ctx = _ctx(
            my_earned=180, opp_earned=100, my_sigma=5, opp_sigma=5, importance=WeekImportance.CRAZY
        )
        posture = determine_posture(ctx, MONDAY)
        assert posture.mode == PostureMode.CONTEST
        assert posture.aggression == AggressionLevel.CONSERVATIVE

    def test_tied_normal(self):
        """Tied with moderate variance -> NORMAL."""
        ctx = _ctx(my_earned=100, opp_earned=95, my_sigma=10, opp_sigma=10)
        assert determine_posture(ctx, MONDAY).aggression == AggressionLevel.NORMAL

    def test_slightly_behind_aggressive(self):
        ctx = _ctx(my_earned=90, opp_earned=100, my_sigma=15, opp_sigma=15)
        assert determine_posture(ctx, MONDAY).aggression == AggressionLevel.AGGRESSIVE

    def test_well_behind_aggressive_not_desperate(self):
        """Down 30 with a lot of variance left is a chase, not an emergency.

        The old engine called this DESPERATE off a 0.25 p_win threshold. Under
        the two-axis model, depth comes from leverage, and 40 player-games of
        remaining variance means one extra point barely moves the needle.
        """
        ctx = _ctx(my_earned=70, opp_earned=100, my_sigma=20, opp_sigma=20)
        assert determine_posture(ctx, MONDAY).aggression == AggressionLevel.AGGRESSIVE

    def test_neutral_caps_at_aggressive(self):
        """A NEUTRAL week never reaches DESPERATE, however high the leverage."""
        ctx = _ctx(
            my_earned=100,
            opp_earned=100,
            my_sigma=4,
            opp_sigma=4,
            importance=WeekImportance.NEUTRAL,
        )
        big = _ctx(my_earned=100, opp_earned=100, my_sigma=4, opp_sigma=4)
        assert determine_posture(big, SUNDAY).aggression == AggressionLevel.DESPERATE
        assert determine_posture(ctx, SUNDAY).aggression == AggressionLevel.AGGRESSIVE

    def test_variance_decay_changes_level(self):
        """Same gap yields different postures at different variance levels."""
        high_var = _ctx(my_earned=80, opp_earned=100, my_sigma=20, opp_sigma=20)
        low_var = _ctx(my_earned=80, opp_earned=100, my_sigma=4, opp_sigma=4)
        assert determine_posture(high_var, MONDAY).mode != determine_posture(low_var, MONDAY).mode

    def test_opp_pickup_boost_lowers_win_probability(self):
        """The opponent's potential pickups count against us."""
        no_boost = _ctx(my_earned=105, opp_earned=100, my_sigma=10, opp_sigma=10)
        with_boost = _ctx(
            my_earned=105,
            opp_earned=100,
            my_sigma=10,
            opp_sigma=10,
            opp_boost_mu=15,
            opp_boost_sigma=5,
        )
        assert (
            determine_posture(with_boost, MONDAY).p_win < determine_posture(no_boost, MONDAY).p_win
        )

    def test_zero_variance_tied_normal(self):
        """No games left and level -> p_win 0.5, nothing to be done about it."""
        posture = determine_posture(_ctx(my_earned=100, opp_earned=100), MONDAY)
        assert posture.p_win == 0.5
        assert posture.aggression == AggressionLevel.NORMAL

    def test_zero_variance_ahead_punts(self):
        """No games left and ahead -> certain win -> PUNT."""
        posture = determine_posture(_ctx(my_earned=110, opp_earned=100), MONDAY)
        assert posture.p_win == 1.0
        assert posture.mode == PostureMode.PUNT

    def test_manual_importance_override_wins(self):
        """The owner can force CRAZY on a specific week."""
        ctx = _ctx(
            my_earned=50, opp_earned=100, my_sigma=3, opp_sigma=3, importance=WeekImportance.BIG
        )
        forced = determine_posture(ctx, MONDAY, importance=WeekImportance.CRAZY)
        assert forced.mode == PostureMode.CONTEST
        assert forced.importance == WeekImportance.CRAZY


class TestDetermineAggressionShim:
    """The legacy pure function. Must never emit PREPARE."""

    def test_returns_aggression_and_win_probability(self):
        ctx = _ctx(my_earned=100, opp_earned=95, my_sigma=10, opp_sigma=10)
        level, wp = determine_aggression(ctx)
        assert level == AggressionLevel.NORMAL
        assert 0.0 <= wp.p_win <= 1.0

    def test_never_returns_prepare(self):
        for ctx in (
            _ctx(my_earned=50, opp_earned=100, my_sigma=3, opp_sigma=3),
            _ctx(my_earned=180, opp_earned=100, my_sigma=5, opp_sigma=5),
            _ctx(my_earned=100, opp_earned=100, my_sigma=4, opp_sigma=4),
        ):
            level, _ = determine_aggression(ctx)
            assert level != AggressionLevel.PREPARE

    def test_decided_matchups_become_conservative(self):
        """A punted week has no depth to spend, so the shim reports CONSERVATIVE."""
        ctx = _ctx(my_earned=180, opp_earned=100, my_sigma=5, opp_sigma=5)
        level, _ = determine_aggression(ctx)
        assert level == AggressionLevel.CONSERVATIVE


class TestAutoImportance:
    def test_in_playoffs(self):
        assert auto_importance(1, 8, True) == WeekImportance.CRAZY

    def test_on_bubble(self):
        assert auto_importance(7, 8, False) == WeekImportance.BIG

    def test_on_bubble_final_weeks(self):
        assert auto_importance(7, 8, False, weeks_remaining=2) == WeekImportance.CRAZY

    def test_on_bubble_early_season(self):
        assert auto_importance(7, 8, False, weeks_remaining=10) == WeekImportance.BIG

    def test_safely_in(self):
        assert auto_importance(4, 8, False) == WeekImportance.NEUTRAL

    def test_out_of_playoffs(self):
        assert auto_importance(10, 8, False) == WeekImportance.BIG


class TestAutoImportanceFromStandingsGap:
    """Rank alone cannot tell a live race from a settled one. The gap can."""

    def test_gap_overrides_rank_when_the_race_is_settled(self):
        """Rank says bubble, but eight wins back with three weeks left is over."""
        assert (
            auto_importance(7, 8, False, weeks_remaining=3, wins_from_cutoff=-8.0)
            == WeekImportance.NEUTRAL
        )

    def test_gap_overrides_rank_when_the_race_is_live(self):
        """Rank says safely in, but a one-win cushion with four weeks left is not."""
        assert (
            auto_importance(3, 8, False, weeks_remaining=4, wins_from_cutoff=1.0)
            == WeekImportance.BIG
        )

    def test_same_gap_matters_more_with_fewer_weeks_left(self):
        """Three points out with six weeks left is not three points out with one."""
        early = auto_importance(7, 8, False, weeks_remaining=6, wins_from_cutoff=-3.0)
        late = auto_importance(7, 8, False, weeks_remaining=2, wins_from_cutoff=-3.0)
        assert early == WeekImportance.BIG
        assert late == WeekImportance.NEUTRAL  # three wins back, two weeks: gone

    def test_dead_heat_in_the_final_weeks_is_crazy(self):
        assert (
            auto_importance(7, 8, False, weeks_remaining=2, wins_from_cutoff=0.0)
            == WeekImportance.CRAZY
        )

    def test_ties_count_as_half_a_win(self):
        assert (
            auto_importance(5, 8, False, weeks_remaining=5, wins_from_cutoff=0.5)
            == WeekImportance.BIG
        )

    def test_large_cushion_is_neutral(self):
        assert (
            auto_importance(2, 8, False, weeks_remaining=4, wins_from_cutoff=6.0)
            == WeekImportance.NEUTRAL
        )


class TestAutoImportanceWithByes:
    """Finishing 1st beats finishing 4th when the top seeds get a bye."""

    def test_bye_race_makes_a_safe_playoff_week_matter(self):
        assert (
            auto_importance(
                3,
                8,
                False,
                weeks_remaining=4,
                wins_from_cutoff=6.0,  # playoffs locked up
                bye_spots=2,
                wins_from_bye_cutoff=-1.0,  # one win off a bye
            )
            == WeekImportance.BIG
        )

    def test_no_bye_race_leaves_a_safe_week_neutral(self):
        assert (
            auto_importance(
                3,
                8,
                False,
                weeks_remaining=4,
                wins_from_cutoff=6.0,
                bye_spots=2,
                wins_from_bye_cutoff=-7.0,
            )
            == WeekImportance.NEUTRAL
        )

    def test_byes_are_ignored_when_the_league_has_none(self):
        assert (
            auto_importance(
                3,
                8,
                False,
                weeks_remaining=4,
                wins_from_cutoff=6.0,
                bye_spots=0,
                wins_from_bye_cutoff=0.0,
            )
            == WeekImportance.NEUTRAL
        )

    def test_bye_rank_fallback_without_a_gap(self):
        """No standings gap available: rank near the bye line still reads BIG."""
        assert auto_importance(3, 8, False, bye_spots=2) == WeekImportance.BIG
        assert auto_importance(7, 8, False, bye_spots=2) == WeekImportance.BIG

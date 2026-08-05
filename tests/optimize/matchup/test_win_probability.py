"""Tests for win probability calculation."""

from datetime import date

import pytest

from src.optimize.matchup.win_probability import compute_win_probability
from src.optimize.models import (
    MatchupContext,
    MatchupSnapshot,
    PickupBoost,
    TeamProjection,
)
from src.optimize.week.variance import compute_team_sigma


def _snapshot(**overrides):
    defaults = dict(
        my_team_key="t1",
        opp_team_key="t2",
        my_earned=0.0,
        opp_earned=0.0,
        week_start=date(2026, 1, 5),
        week_end=date(2026, 1, 11),
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
    **kw,
):
    return MatchupContext(
        snapshot=_snapshot(my_earned=my_earned, opp_earned=opp_earned),
        my_projection=_projection("t1", my_earned, my_mu, my_sigma),
        opp_projection=_projection("t2", opp_earned, opp_mu, opp_sigma),
        my_pickup_boost=_no_boost(),
        opp_pickup_boost=PickupBoost(opp_boost_mu, opp_boost_sigma, 4 if opp_boost_mu else 0),
        **kw,
    )


class TestComputeWinProbability:
    def test_tied_zero_variance_is_coin_flip(self):
        ctx = _ctx(my_earned=100, opp_earned=100)
        wp = compute_win_probability(ctx)
        assert wp.p_win == 0.5

    def test_leading_zero_variance_is_certain_win(self):
        ctx = _ctx(my_earned=150, opp_earned=100)
        wp = compute_win_probability(ctx)
        assert wp.p_win == 1.0

    def test_trailing_zero_variance_is_certain_loss(self):
        ctx = _ctx(my_earned=80, opp_earned=120)
        wp = compute_win_probability(ctx)
        assert wp.p_win == 0.0

    def test_large_lead_high_variance_not_certain(self):
        ctx = _ctx(my_earned=100, opp_earned=60, my_sigma=20, opp_sigma=20)
        wp = compute_win_probability(ctx)
        assert 0.85 < wp.p_win < 1.0

    def test_large_deficit_high_variance_not_zero(self):
        ctx = _ctx(my_earned=60, opp_earned=100, my_sigma=20, opp_sigma=20)
        wp = compute_win_probability(ctx)
        assert 0.0 < wp.p_win < 0.15

    def test_same_gap_less_variance_more_extreme_p_win(self):
        ctx_high_var = _ctx(my_earned=120, opp_earned=100, my_sigma=25, opp_sigma=25)
        ctx_low_var = _ctx(my_earned=120, opp_earned=100, my_sigma=5, opp_sigma=5)
        wp_high = compute_win_probability(ctx_high_var)
        wp_low = compute_win_probability(ctx_low_var)
        assert wp_low.p_win > wp_high.p_win

    def test_opp_pickup_boost_lowers_p_win(self):
        ctx_no_boost = _ctx(my_earned=110, opp_earned=100, my_sigma=10, opp_sigma=10)
        ctx_with_boost = _ctx(
            my_earned=110,
            opp_earned=100,
            my_sigma=10,
            opp_sigma=10,
            opp_boost_mu=15,
            opp_boost_sigma=5,
        )
        wp_no = compute_win_probability(ctx_no_boost)
        wp_yes = compute_win_probability(ctx_with_boost)
        assert wp_yes.p_win < wp_no.p_win

    def test_remaining_points_added_to_earned(self):
        ctx = _ctx(my_earned=50, my_mu=60, opp_earned=50, opp_mu=40, my_sigma=10, opp_sigma=10)
        wp = compute_win_probability(ctx)
        assert wp.my_total == pytest.approx(110)
        assert wp.opp_total == pytest.approx(90)
        assert wp.projected_gap == pytest.approx(20)

    def test_reasoning_populated(self):
        ctx = _ctx(my_earned=100, opp_earned=90, my_sigma=10, opp_sigma=10)
        wp = compute_win_probability(ctx)
        assert len(wp.reasoning) > 0
        assert any("P(win)" in r for r in wp.reasoning)


class TestComputeTeamSigma:
    def test_empty_returns_zero(self):
        assert compute_team_sigma([]) == 0.0

    def test_single_game(self):
        """One game's sigma is whatever the fitted skater curve says."""
        from src.optimize.models import PlayerType
        from src.optimize.week.variance import game_sigma

        assert compute_team_sigma([5.0]) == pytest.approx(game_sigma(5.0, PlayerType.SKATER))

    def test_multiple_games(self):
        """Games are independent, so variances add."""
        from src.optimize.models import PlayerType
        from src.optimize.week.variance import game_sigma

        one = game_sigma(5.0, PlayerType.SKATER)
        assert compute_team_sigma([5.0, 5.0, 5.0]) == pytest.approx((3 * one**2) ** 0.5)

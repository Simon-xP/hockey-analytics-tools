"""Acceptance tests from docs/plans/weekly-optimizer/04a-goalie-variance.md.

The pure-arithmetic tests run anywhere. The calibration and leakage tests
hit the real dev database and skip cleanly when the goalie game log is not
loaded.

The calibration test is allowed to veto the work: if 80% intervals do not
contain about 80% of outcomes, that is a finding to report, not a number to
tune until it passes.
"""

from datetime import date

import numpy as np
import pytest

from src.predict.goalies.constants import OUTCOME_VAR
from src.predict.goalies.save_quality import shrink_save_rate
from src.predict.goalies.start_value import StartInputs, project_start_value
from src.predict.goalies.variance import GoalieDayForecast, combine

# A skater with a comparable mean. The brief puts skater sd at 1.5 to 2.0
# for a 3.5-point mean; 2.0 is the generous end, so using it makes the
# goalie comparison harder to pass rather than easier.
SKATER_MEAN = 3.5
SKATER_SD = 2.0


def _forecast(p_start: float, start_value: float, **kw) -> GoalieDayForecast:
    return GoalieDayForecast(
        nhl_id=1, game_date=date(2026, 1, 15), game_id=None,
        p_start=p_start, start_value=start_value,
        outcome_var=kw.pop("outcome_var", OUTCOME_VAR),
        confidence=kw.pop("confidence", 1.0), **kw,
    )


class TestVarianceShape:
    """The formula itself. If these fail, nothing downstream matters."""

    def test_goalie_sd_exceeds_skater_sd_at_equal_mean(self):
        """A goalie and a skater with the same mean are not equally risky.

        The headline claim. Matched on expected points, the goalie carries
        materially more spread, which is why goalie streams behave
        differently under a win-probability objective.
        """
        # p = 0.5 on a 7-point start gives the same 3.5 mean as the skater.
        goalie = _forecast(p_start=0.5, start_value=SKATER_MEAN * 2)

        assert goalie.mean == pytest.approx(SKATER_MEAN)
        assert goalie.sd > SKATER_SD, (
            f"goalie sd {goalie.sd:.2f} does not exceed skater sd "
            f"{SKATER_SD:.2f} at an equal mean of {SKATER_MEAN}"
        )
        # And by a wide margin, not a rounding error.
        assert goalie.sd / SKATER_SD > 1.5

    def test_coin_flip_is_riskier_than_confirmed_starter_of_equal_ev(self):
        """Proves the Bernoulli term is actually wired in.

        A p=0.5 goalie worth 7 and a p=1.0 goalie worth 3.5 have identical
        means. Only a model carrying the start-uncertainty term can tell
        them apart, and the coin flip must be the riskier asset.
        """
        coin_flip = _forecast(p_start=0.5, start_value=7.0)
        confirmed = _forecast(p_start=1.0, start_value=3.5)

        assert coin_flip.mean == pytest.approx(confirmed.mean)
        assert coin_flip.variance > confirmed.variance

        # Materially higher, not a rounding artifact. With the fitted
        # OUTCOME_VAR of 16.87 the measured ratio is 1.23: the coin flip
        # carries 23% more variance for the same expected points. The floor
        # is set below that rather than at it so a small refit of the
        # constant does not break the test, but well above 1.0 so a missing
        # Bernoulli term still fails loudly.
        ratio = coin_flip.variance / confirmed.variance
        assert ratio > 1.15, (
            f"coin flip var {coin_flip.variance:.2f} vs confirmed "
            f"{confirmed.variance:.2f} (ratio {ratio:.3f}): the Bernoulli "
            f"term looks absent or understated"
        )

    def test_start_term_vanishes_at_certainty(self):
        """No start uncertainty when the start is certain, either way."""
        assert _forecast(p_start=0.0, start_value=7.0).start_term == 0.0
        assert _forecast(p_start=1.0, start_value=7.0).start_term == 0.0

        # And a certain non-start contributes nothing at all.
        never = _forecast(p_start=0.0, start_value=7.0)
        assert never.mean == 0.0
        assert never.variance == 0.0

    def test_variance_peaks_at_intermediate_p(self):
        """Start uncertainty is maximal in the middle, for fixed value."""
        value = 7.0
        ps = np.linspace(0.0, 1.0, 101)
        start_terms = [_forecast(p, value).start_term for p in ps]

        peak = ps[int(np.argmax(start_terms))]
        assert peak == pytest.approx(0.5, abs=0.02)

        # Total variance also peaks strictly inside the interval, since the
        # start term dominates for a value this size.
        totals = [_forecast(p, value).variance for p in ps]
        assert max(totals) > totals[-1]
        assert 0.0 < ps[int(np.argmax(totals))] < 1.0

    def test_combine_matches_the_documented_formula(self):
        """combine() and the dataclass cannot drift apart."""
        for p, m in [(0.0, 5.0), (0.37, 6.2), (0.5, 7.0), (1.0, 4.1)]:
            mean, var = combine(p, m, OUTCOME_VAR)
            f = _forecast(p, m)
            assert mean == pytest.approx(f.mean)
            assert var == pytest.approx(f.variance)
            assert var == pytest.approx(p * OUTCOME_VAR + p * (1 - p) * m**2)


class TestCeiling:
    """The "we need a ceiling" case, expressed at the variance level.

    The brief's two Thursday options: a confirmed weak goalie worth 5.0 at
    p=1.0, against an elite goalie worth 8.0 in a timeshare at p=0.5. The
    elite has the lower mean (4.0 against 5.0) and should nonetheless be the
    right pick when trailing late, because of a fatter upper tail.

    That property does hold. The brief's specific threshold of 7 points does
    not demonstrate it, and cannot: see the xfail below.
    """

    CONFIRMED_WEAK = (1.00, 5.0)
    TIMESHARE_ELITE = (0.50, 8.0)

    def test_ceiling_favors_the_timeshare_elite(self):
        """The elite timeshare dominates the upper tail."""
        weak = _forecast(*self.CONFIRMED_WEAK)
        elite = _forecast(*self.TIMESHARE_ELITE)

        assert weak.mean > elite.mean, "premise: the elite has the lower mean"

        # Above the crossover the ordering inverts and stays inverted, which
        # is the behaviour the optimizer needs when it is chasing a ceiling.
        for threshold in (8.0, 9.0, 10.0, 12.0, 14.0):
            p_weak = weak.prob_exceeds(threshold)
            p_elite = elite.prob_exceeds(threshold)
            assert p_elite > p_weak, (
                f"at {threshold} points P(>x) is {p_elite:.3f} for the "
                f"timeshare elite against {p_weak:.3f} for the confirmed "
                f"weak goalie: the ceiling ordering is inverted"
            )

        # And the advantage widens the further into the tail you go, which
        # is what makes it a ceiling play rather than a coin flip.
        near = elite.prob_exceeds(9.0) / weak.prob_exceeds(9.0)
        far = elite.prob_exceeds(14.0) / weak.prob_exceeds(14.0)
        assert far > near

    def test_crossover_sits_just_above_the_briefs_threshold(self):
        """Pin down where the ordering flips, since the brief's 7 is below it.

        Documented as a test so the number cannot silently drift: if a refit
        of OUTCOME_VAR moves the crossover far from here, that is worth
        knowing.
        """
        weak = _forecast(*self.CONFIRMED_WEAK)
        elite = _forecast(*self.TIMESHARE_ELITE)

        lo, hi = 5.0, 20.0
        for _ in range(200):
            mid = (lo + hi) / 2.0
            if elite.prob_exceeds(mid) > weak.prob_exceeds(mid):
                hi = mid
            else:
                lo = mid
        crossover = (lo + hi) / 2.0

        assert 7.0 < crossover < 8.0, f"crossover moved to {crossover:.2f}"

    @pytest.mark.xfail(
        reason=(
            "The brief specifies a threshold of 7 points, which is below the "
            "crossover and so shows the opposite of what it intends. This is "
            "an error in the example, not in the model: at 7 points the "
            "confirmed weak goalie's own upside tail already clears the bar "
            "often enough (0.313) that halving the elite's probability "
            "(0.596 -> 0.298) dominates. The crossover is 7.41 with the "
            "fitted OUTCOME_VAR of 16.87, and 8.10 with the sd of 4.5 the "
            "brief itself quotes, so the example fails on its own numbers "
            "too. Kept as xfail rather than deleted so the discrepancy stays "
            "visible; test_ceiling_favors_the_timeshare_elite covers the "
            "property the brief actually wants."
        ),
        strict=True,
    )
    def test_ceiling_at_the_briefs_literal_threshold(self):
        weak = _forecast(*self.CONFIRMED_WEAK)
        elite = _forecast(*self.TIMESHARE_ELITE)
        assert elite.prob_exceeds(7.0) > weak.prob_exceeds(7.0)


class TestConfidenceIsNotPStart:
    def test_same_p_start_can_carry_different_confidence(self):
        """A settled timeshare and no-information must be distinguishable.

        Identical mean and variance, different confidence, because they
        call for opposite actions: plan around the first, wait on the
        second.
        """
        settled = _forecast(p_start=0.5, start_value=7.0, confidence=0.70)
        unknown = _forecast(p_start=0.5, start_value=7.0, confidence=0.10)

        assert settled.mean == pytest.approx(unknown.mean)
        assert settled.variance == pytest.approx(unknown.variance)
        assert settled.confidence > unknown.confidence


class TestShrinkage:
    def test_recent_form_is_shrunk(self):
        """Ten elite starts must not move the projection much.

        Ten starts is roughly 280 shots. Against a credibility constant of
        1800 that earns about 13% weight, so a goalie who has stopped
        everything for ten games should still project near baseline. An
        unshrunk model would hand back their raw rate and chase noise.
        """
        league = 0.900
        # Ten starts, ~28 shots each, at a wildly elite .950.
        elite_ten = shrink_save_rate(
            goalie_shots=280, goalie_saves=266,
            team_shots=1500, team_saves=1350,   # team at league average
            league_rate=league, current_league_rate=league,
        )

        assert elite_ten.raw_save_rate == pytest.approx(0.950, abs=0.001)
        assert elite_ten.credibility < 0.20

        # Should sit far closer to baseline than to its own raw rate.
        to_baseline = abs(elite_ten.save_rate - league)
        to_raw = abs(elite_ten.save_rate - elite_ten.raw_save_rate)
        assert to_baseline < to_raw, (
            f"shrunk estimate {elite_ten.save_rate:.4f} is closer to the raw "
            f"{elite_ten.raw_save_rate:.4f} than to baseline {league:.4f}"
        )
        # Roughly: 0.900 + 0.13 * 0.050 = about .9065
        assert elite_ten.save_rate == pytest.approx(0.9065, abs=0.004)

    def test_a_full_career_earns_more_weight_than_ten_starts(self):
        """Shrinkage relaxes with evidence, and does so on shots not starts."""
        league = 0.900
        ten = shrink_save_rate(280, 266, 1500, 1350, league, league)
        career = shrink_save_rate(6000, 5700, 1500, 1350, league, league)

        assert career.credibility > ten.credibility
        assert abs(career.save_rate - league) > abs(ten.save_rate - league)

    def test_fifty_starts_stays_under_half_weight(self):
        """The brief's explicit ceiling on how fast shrinkage may relax."""
        fifty_starts_shots = 50 * 28
        q = shrink_save_rate(fifty_starts_shots, fifty_starts_shots * 0.92,
                             1500, 1350, 0.900, 0.900)
        assert q.credibility < 0.5, (
            f"50 starts earns {q.credibility:.3f} weight, over the one-half "
            f"ceiling the brief sets"
        )

    def test_shrinkage_is_era_relative(self):
        """A goalie's edge transfers; their absolute rate does not.

        Earned .910 in a .900 league, projected into a .890 league, the
        estimate must land below .910. Carrying the absolute rate forward is
        what over-projected every goalie by 0.76 points per start.
        """
        q = shrink_save_rate(
            goalie_shots=6000, goalie_saves=5460,      # .910
            team_shots=3000, team_saves=2700,          # league average
            league_rate=0.900, current_league_rate=0.890,
        )
        assert q.save_rate < 0.910
        assert q.save_rate < 0.900   # below the old league level too
        assert q.save_rate > 0.890   # but still above the new league mean


class TestOwnTeamOffense:
    @staticmethod
    def _inputs(own_gf: float, own_ga: float = 3.0) -> StartInputs:
        """Identical matchup, only the goalie's own team's scoring varies."""
        return StartInputs(
            team_sa_per_start=28.0,
            opp_sf_per_game=28.0,
            league_sa_per_start=28.0,
            window_sa_per_start=28.0,
            save_rate=0.900,
            league_save_rate=0.900,
            own_gf_per_game=own_gf,
            opp_ga_per_game=3.0,
            own_ga_per_game=own_ga,
            opp_gf_per_game=3.0,
            league_gf_per_game=3.0,
            window_gf_per_game=3.0,
            is_home=False,
        )

    def test_own_team_offense_moves_the_projection(self):
        """Wins come from your team scoring, so offense must be an input.

        Same goalie, same opponent, swap the team between high and low
        scoring. If the projection does not move, the win component is not
        wired to own-team offense and streaming will be biased toward
        defensive teams all season.
        """
        high = project_start_value(self._inputs(own_gf=3.6))
        low = project_start_value(self._inputs(own_gf=2.5))

        assert high.win_prob > low.win_prob
        assert high.start_value > low.start_value, (
            "own-team offense does not move start_value: the win component "
            "is not reading it"
        )
        # The gap should be worth something real, not a rounding artifact.
        assert high.start_value - low.start_value > 0.5

    def test_shots_faced_is_unaffected_by_own_offense(self):
        """Offense changes wins, not workload. Guards against a smeared fix."""
        high = project_start_value(self._inputs(own_gf=3.6))
        low = project_start_value(self._inputs(own_gf=2.5))
        assert high.shots_against == pytest.approx(low.shots_against)
        assert high.goals_against == pytest.approx(low.goals_against)

    def test_opponent_offense_moves_workload(self):
        """The other half: a high-volume opponent means more shots faced."""
        base = self._inputs(own_gf=3.0)
        busy = StartInputs(**{**base.__dict__, "opp_sf_per_game": 32.0})
        quiet = StartInputs(**{**base.__dict__, "opp_sf_per_game": 24.0})

        assert (project_start_value(busy).shots_against
                > project_start_value(quiet).shots_against)

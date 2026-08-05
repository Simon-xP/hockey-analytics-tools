"""Tests for the fantasy-point variance model.

The last one is the important one. `calibration_check` is what says the
variance model is trustworthy enough to run a `P(win)` objective on top of.
If it fails, the honest response is to say so, not to tune until it passes.
"""

from pathlib import Path

import pytest

from src.optimize.models import PlayerType
from src.optimize.week.variance import (
    SKATER_SIGMA_INTERCEPT,
    SKATER_SIGMA_SLOPE,
    compute_team_sigma,
    game_sigma,
    team_sigma,
)

RESIDUALS = Path("data/variance_residuals.csv")


class TestGameSigma:
    def test_sigma_is_monotonic_in_mu(self):
        sigmas = [game_sigma(mu, PlayerType.SKATER) for mu in (0.0, 1.0, 3.0, 6.0, 12.0)]
        assert sigmas == sorted(sigmas)
        assert all(b > a for a, b in zip(sigmas, sigmas[1:]))

    def test_a_fringe_player_still_carries_real_noise(self):
        """A pure CV model says a 0.5-FPTS player has almost no variance.

        Peripherals alone produce more scatter than that, and understating it
        would make the whole streaming pool look artificially safe.
        """
        assert game_sigma(0.5, PlayerType.SKATER) > 0.5

    def test_negative_projections_are_clamped_not_reflected(self):
        assert game_sigma(-3.0, PlayerType.SKATER) == pytest.approx(
            SKATER_SIGMA_INTERCEPT
        )

    def test_the_curve_is_the_fitted_affine_form(self):
        assert game_sigma(4.0, PlayerType.SKATER) == pytest.approx(
            SKATER_SIGMA_INTERCEPT + SKATER_SIGMA_SLOPE * 4.0
        )

    def test_goalies_currently_share_the_skater_curve(self):
        """P3 owns the goalie coefficients; until they land this is the fallback."""
        assert game_sigma(6.0, PlayerType.GOALIE) == pytest.approx(
            game_sigma(6.0, PlayerType.SKATER)
        )


class TestTeamSigma:
    def test_sigma_of_empty_window_is_zero(self):
        assert team_sigma([]) == 0.0
        assert compute_team_sigma([]) == 0.0

    def test_variances_add_across_games(self):
        one = game_sigma(4.0, PlayerType.SKATER)
        assert team_sigma([4.0, 4.0]) == pytest.approx((2 * one**2) ** 0.5)

    def test_more_games_never_lowers_sigma(self):
        assert team_sigma([3.0, 3.0]) > team_sigma([3.0])

    def test_compute_team_sigma_is_the_same_function(self):
        assert compute_team_sigma is team_sigma


class TestCalibration:
    """The gate on shipping a P(win) objective at all."""

    @pytest.fixture(scope="class")
    def residuals(self):
        if not RESIDUALS.exists():
            pytest.skip(
                "no measured residuals; run "
                "`python -m scripts.fit_variance_model harvest`"
            )
        import csv

        import numpy as np

        projected, actual = [], []
        with RESIDUALS.open() as fh:
            for row in csv.DictReader(fh):
                projected.append(float(row["projected_fpts"]))
                actual.append(float(row["actual_fpts"]))
        if len(projected) < 2000:
            pytest.skip("residual sample too small to calibrate against")
        return np.array(projected), np.array(actual)

    def test_calibration_check(self, residuals):
        """80% of synthetic weekly totals should land inside the 80% interval.

        A fantasy week is roughly 25 player-games. Draw that many, sum the
        projections and the actuals, and see how often the actual total falls
        inside the model's interval.

        Residuals are de-meaned first. The projection carries a known
        systematic bias (it over-projects by roughly half a point per game —
        see `scripts/fit_variance_model.py` and the note in
        `week/variance.py`), and that bias belongs to `src/predict/`, not to
        the variance model. Leaving it in would make this test measure the
        forecast's accuracy rather than its dispersion.
        """
        import numpy as np

        projected, actual = residuals
        resid = actual - projected
        resid = resid - resid.mean()

        rng = np.random.default_rng(20260802)
        z = 1.2815515655446004  # 90th percentile of the standard normal
        inside = 0
        trials = 4000
        for _ in range(trials):
            idx = rng.integers(0, len(projected), size=25)
            total_sigma = float(
                np.sqrt(
                    sum(game_sigma(m, PlayerType.SKATER) ** 2 for m in projected[idx])
                )
            )
            if abs(resid[idx].sum()) <= z * total_sigma:
                inside += 1

        coverage = inside / trials
        assert 0.75 <= coverage <= 0.85, (
            f"80% interval covers {coverage:.1%} of weekly totals. The variance "
            "model is not calibrated and a P(win) objective built on it will be "
            "systematically over- or under-confident."
        )

"""Calibration and leakage tests against the real dev database.

These skip cleanly when the goalie game log is not loaded.

The calibration test is allowed to veto the work. A miscalibrated variance
under a win-probability objective produces confidently wrong risk decisions,
which is worse than no goalie support at all, so the honest move on failure
is to report the coverage rather than adjust until it passes.
"""

from datetime import date, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import text

from src.core.db import get_session
from src.core.models import GoalieStart
from src.core.queries.goalie_starts import latest_start_reports
from src.predict.goalies.constants import OUTCOME_VAR
from src.predict.goalies.save_quality import estimate_save_quality
from src.predict.goalies.start_value import forecast_start_value
from src.predict.goalies.starts import crease_share
from src.predict.goalies.variance import _normal_quantile

TEST_SEASONS = {"20242025", "20252026"}
CUTOFF = date(2026, 1, 15)


@pytest.fixture(scope="module")
def walk_forward_results():
    """Out-of-sample projections for every start in the test seasons.

    Reuses the fitter so the calibration check measures the same model that
    produced the constant, not a re-implementation of it.
    """
    from scripts.fit_goalie_variance import load_starts, walk_forward

    with get_session() as session:
        loaded = session.execute(
            text("SELECT count(*) FROM goalie_game_log WHERE is_start")
        ).scalar()
        if not loaded or loaded < 5000:
            pytest.skip("goalie_game_log not populated; run "
                        "scripts.build_goalie_game_log")
        all_starts = load_starts(session)

    results = walk_forward(all_starts, TEST_SEASONS)
    if len(results) < 1000:
        pytest.skip("too few walk-forward projections to assess calibration")
    return results


class TestCalibration:
    def test_eighty_percent_interval_covers_about_eighty_percent(
        self, walk_forward_results
    ):
        """The veto test. Coverage of the conditional 80% interval.

        Conditional on a start, so `p_start` is not involved: this isolates
        whether `outcome_var` itself is honest.
        """
        residual = np.array([r["actual"] - r["projected"]
                             for r in walk_forward_results])
        z = _normal_quantile(0.90)
        half_width = z * np.sqrt(OUTCOME_VAR)
        coverage = float(np.mean(np.abs(residual) <= half_width))

        assert 0.75 <= coverage <= 0.85, (
            f"80% interval covers {coverage:.3f} of {len(residual)} outcomes, "
            f"outside the acceptable 0.75 to 0.85 band. The variance is "
            f"{'overstated' if coverage > 0.85 else 'understated'}. Report "
            f"this rather than tuning OUTCOME_VAR to hit the band."
        )

    def test_projections_are_not_systematically_biased(
        self, walk_forward_results
    ):
        """A calibrated variance around a biased mean is still wrong.

        Guards the calibration offset. Tolerance is 0.15 points, roughly 3%
        of the mean and well inside the noise on 5,000 starts.
        """
        residual = np.array([r["actual"] - r["projected"]
                             for r in walk_forward_results])
        bias = float(residual.mean())
        se = float(residual.std(ddof=1) / np.sqrt(len(residual)))

        assert abs(bias) < 0.15, (
            f"mean residual is {bias:+.3f} points (se {se:.3f}). "
            f"Re-fit START_VALUE_OFFSET."
        )

    def test_low_projection_quartile_is_not_understated(
        self, walk_forward_results
    ):
        """The test that justifies a constant over a proportional form.

        A proportional form would scale variance with the projection and so
        understate the bottom quartile, where the spread is in fact just as
        wide.
        """
        projected = np.array([r["projected"] for r in walk_forward_results])
        residual = np.array([r["actual"] - r["projected"]
                             for r in walk_forward_results])

        order = np.argsort(projected)
        bottom = order[: len(order) // 4]
        bottom_var = float(residual[bottom].var(ddof=1))

        ratio = bottom_var / OUTCOME_VAR
        assert 0.85 <= ratio <= 1.15, (
            f"bottom-quartile variance is {bottom_var:.2f} against a constant "
            f"of {OUTCOME_VAR:.2f} ({ratio:.3f}x): the constant no longer "
            f"describes low projections"
        )

        # And confirm the proportional alternative really would be worse.
        scale = (projected[bottom].mean() / projected.mean()) ** 2
        proportional = OUTCOME_VAR * scale
        assert abs(bottom_var - OUTCOME_VAR) < abs(bottom_var - proportional)


class TestLeakage:
    """A decision at `as_of` may not see anything from `as_of` onward."""

    def test_start_probability_cannot_see_later_reports(self):
        """Appended reports are invisible until their observation time.

        The failure this guards against is silent: an upserting table lets a
        Monday decision read Thursday afternoon's confirmation, every goalie
        stream looks inspired, and the measured edge is pure leakage.
        """
        with get_session() as session:
            game = session.execute(
                text("""
                    SELECT game_id, team_id, goalie_id
                    FROM goalie_game_log
                    WHERE is_start AND game_date = :d
                    LIMIT 1
                """),
                {"d": CUTOFF},
            ).fetchone()
            if not game:
                pytest.skip(f"no goalie starts on {CUTOFF}")

            game_id, team_id, goalie_id = game
            morning = datetime.combine(CUTOFF, datetime.min.time()).replace(hour=10)
            afternoon = morning.replace(hour=16)

            session.query(GoalieStart).filter(
                GoalieStart.game_id == game_id,
                GoalieStart.source == "pytest",
            ).delete()

            session.add(GoalieStart(
                game_id=game_id, team_id=team_id, nhl_id=goalie_id,
                confirmed=True, confirmation="Confirmed",
                source="pytest", observed_at=afternoon,
            ))
            session.flush()

            try:
                before = latest_start_reports(
                    session, as_of=morning, game_ids=[game_id])
                after = latest_start_reports(
                    session, as_of=afternoon + timedelta(minutes=1),
                    game_ids=[game_id])

                assert (game_id, goalie_id) not in before, (
                    "a report observed at 16:00 was visible to a decision "
                    "made at 10:00"
                )
                assert (game_id, goalie_id) in after
            finally:
                session.rollback()

    def test_start_value_cannot_see_games_at_or_after_as_of(self):
        """Widening `as_of` must change the projection, never the reverse."""
        with get_session() as session:
            row = session.execute(
                text("""
                    SELECT goalie_id, team_id, opponent_team_id, is_home
                    FROM goalie_game_log
                    WHERE is_start AND game_date = :d
                    LIMIT 1
                """),
                {"d": CUTOFF},
            ).fetchone()
            if not row:
                pytest.skip(f"no goalie starts on {CUTOFF}")
            goalie_id, team_id, opp_id, is_home = row

            early = forecast_start_value(
                session, goalie_id, team_id, opp_id,
                as_of=CUTOFF, is_home=is_home)
            late = forecast_start_value(
                session, goalie_id, team_id, opp_id,
                as_of=CUTOFF + timedelta(days=60), is_home=is_home)

        # Different information must produce a different projection. If they
        # match exactly, `as_of` is not reaching the queries.
        assert early.start_value != pytest.approx(late.start_value, abs=1e-9), (
            "projections at as_of and as_of + 60 days are identical: the "
            "temporal gate is not wired through"
        )

    def test_save_quality_exposure_grows_with_as_of(self):
        """Shots faced is monotone in `as_of`, and bounded by it."""
        with get_session() as session:
            # Must be inside the three-season lookback window, not merely
            # before the cutoff: an unqualified pick lands on someone like
            # Luongo whose starts are all a decade old, where zero visible
            # shots is the correct answer and the test proves nothing.
            row = session.execute(
                text("""
                    SELECT goalie_id, team_id FROM goalie_game_log
                    WHERE is_start AND game_date < :d AND game_date >= :since
                    GROUP BY goalie_id, team_id
                    HAVING count(*) >= 20
                    ORDER BY count(*) DESC
                    LIMIT 1
                """),
                {"d": CUTOFF, "since": date(2022, 8, 1)},
            ).fetchone()
            if not row:
                pytest.skip("no goalie with enough starts inside the window")
            goalie_id, team_id = row

            early = estimate_save_quality(session, goalie_id, team_id, CUTOFF)
            late = estimate_save_quality(
                session, goalie_id, team_id, CUTOFF + timedelta(days=60))

            # The count of shots strictly before the cutoff, computed
            # independently of the module under test.
            expected = session.execute(
                text("""
                    SELECT COALESCE(SUM(shots_against), 0)
                    FROM goalie_game_log
                    WHERE goalie_id = :g AND is_start
                      AND game_date < :d AND game_date >= :since
                """),
                {"g": goalie_id, "d": CUTOFF, "since": date(2022, 8, 1)},
            ).scalar()

        assert late.shots_faced > early.shots_faced
        assert early.shots_faced == expected, (
            f"save quality saw {early.shots_faced} shots but only {expected} "
            f"were recorded before {CUTOFF}"
        )

    def test_crease_share_is_bounded_by_as_of(self):
        """Crease share must filter on date, not on a game_id range.

        The known bug in `src/optimize/goalies.py` bounds by `game_id` and so
        sees the whole season regardless of `as_of`. This asserts the
        replacement does not repeat it.
        """
        with get_session() as session:
            row = session.execute(
                text("""
                    SELECT goalie_id, team_id FROM goalie_game_log
                    WHERE is_start AND game_date < :d
                    GROUP BY goalie_id, team_id
                    HAVING count(*) >= 15
                    LIMIT 1
                """),
                {"d": CUTOFF},
            ).fetchone()
            if not row:
                pytest.skip("no goalie with enough starts before the cutoff")
            goalie_id, team_id = row

            # A cutoff early enough that the team has played almost nothing.
            early_cutoff = date(2024, 10, 10)
            _, early_games = crease_share(
                session, goalie_id, team_id, early_cutoff)
            _, later_games = crease_share(session, goalie_id, team_id, CUTOFF)

        assert early_games < later_games, (
            f"crease share saw {early_games} games at {early_cutoff} and "
            f"{later_games} at {CUTOFF}: the date filter is not binding"
        )

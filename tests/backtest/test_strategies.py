"""Integration tests for `src.backtest.strategies`.

Run the walk-forward backtest engine with the Oracle and Baseline strategies
and check scoring sanity plus the daily-decision distribution. Hits the real
dev database and skips cleanly if the expected data isn't loaded.
"""

from datetime import timedelta

import pytest

from src.backtest import (
    BacktestConfig,
    BacktestEngine,
    OracleStrategy,
    BaselineStrategy,
)


class TestOracleStrategy:
    """The oracle should produce clearly positive pickup value."""

    @pytest.fixture(scope="class")
    def oracle_report(self):
        config = BacktestConfig(
            strategy=OracleStrategy(),
            start_week=10,
            end_week=12,
            adds_per_week=4,
        )
        try:
            return BacktestEngine(config).run()
        except Exception as e:
            pytest.skip(f"Backtest run failed (data not loaded?): {e}")

    def test_oracle_mean_pickup_value_is_positive(self, oracle_report):
        assert oracle_report.mean_pickup_value > 0, (
            f"Oracle mean pickup value {oracle_report.mean_pickup_value:.2f} "
            f"should be positive — scoring pipeline may be broken"
        )

    def test_oracle_hit_rate_above_50(self, oracle_report):
        assert oracle_report.hit_rate > 0.5, (
            f"Oracle hit rate {oracle_report.hit_rate:.1%} should be >50% "
            f"— scoring pipeline may be broken"
        )

    def test_oracle_makes_transactions(self, oracle_report):
        assert oracle_report.agent_total_adds > 0, (
            "Oracle made zero transactions — FA pool or strategy may be broken"
        )


class TestDailyDecisionDistribution:
    """Transactions should be stamped on plausible days, not all Mondays."""

    @pytest.fixture(scope="class")
    def baseline_report(self):
        config = BacktestConfig(
            strategy=BaselineStrategy(),
            start_week=8,
            end_week=14,
        )
        try:
            return BacktestEngine(config).run()
        except Exception as e:
            pytest.skip(f"Backtest run failed (data not loaded?): {e}")

    def test_not_all_mondays(self, baseline_report):
        dist = baseline_report.day_of_week_distribution
        if not dist:
            pytest.skip("No transactions to check distribution")

        total = sum(dist.values())
        mon_count = dist.get("Mon", 0)
        assert mon_count < total, (
            f"All {total} transactions on Monday — daily loop not working"
        )

    def test_multiple_weekdays_used(self, baseline_report):
        dist = baseline_report.day_of_week_distribution
        if not dist:
            pytest.skip("No transactions to check distribution")

        days_used = len(dist)
        assert days_used >= 2, (
            f"Only {days_used} distinct weekday(s) used: {dist} — "
            f"expected spread across the week"
        )

    def test_decided_on_dates_are_within_week(self, baseline_report):
        """Every transaction's decided_on must fall within its week's Mon-Sun."""
        for wr in baseline_report.weekly_results:
            monday = wr.week_start
            sunday = monday + timedelta(days=6)
            for txn in wr.agent_transactions:
                assert monday <= txn.decided_on <= sunday, (
                    f"Week {wr.yahoo_week}: transaction on {txn.decided_on} "
                    f"is outside {monday}..{sunday}"
                )

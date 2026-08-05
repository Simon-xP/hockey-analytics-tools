"""Multi-season FA pool simulation engine.

Simulates fantasy hockey FA pools across historical seasons to evaluate
transaction strategies with enough sample size (100+ decisions).

For each week in each season, constructs a synthetic FA pool (top N
players by trailing FPTS/GP are "rostered", rest are available), runs
the strategy, and measures pickup quality against actual outcomes.

This is the counterpart to BacktestEngine — it doesn't require Yahoo
data, so it works for any season with GameAdvancedStats loaded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from src.backtest.data_context import BacktestDataContext, build_context
from src.core.queries.stats import StatsProvider
from src.core.queries.schedule import ScheduleProvider
from src.backtest.report import SimulationReport, SimulationTransaction
from src.backtest.strategies import TransactionStrategy, BaselineStrategy
from src.core.db import get_session
from src.optimize.models import AggressionLevel

DEFAULT_SEASONS = ["20212022", "20222023", "20232024", "20242025", "20252026"]


@dataclass
class SimulationConfig:
    seasons: list[str] = field(default_factory=lambda: list(DEFAULT_SEASONS))
    strategy: TransactionStrategy = field(default_factory=BaselineStrategy)
    n_rostered: int = 160
    adds_per_week: int = 3
    alpha: float = 0.5
    skip_first_weeks: int = 4


class SimulationEngine:
    """Multi-season FA pool simulation with pluggable strategies."""

    def __init__(self, config: SimulationConfig):
        self.config = config

    def run(self) -> SimulationReport:
        all_transactions: list[SimulationTransaction] = []

        with get_session() as session:
            for season in self.config.seasons:
                print(f"Simulating {season}...")
                txns = self._simulate_season(session, season)
                all_transactions.extend(txns)

            print(f"\nTotal transactions: {len(all_transactions)}")

            if not all_transactions:
                return SimulationReport(
                    strategy_name=self.config.strategy.name,
                    seasons=self.config.seasons,
                    n_rostered=self.config.n_rostered,
                    alpha=self.config.alpha,
                )

            print("Evaluating transactions...")
            self._evaluate_transactions(session, all_transactions)

        report = self._build_report(all_transactions)
        print("\n" + report.summary())
        return report

    def _simulate_season(
        self,
        session: Session,
        season: str,
    ) -> list[SimulationTransaction]:
        schedule = ScheduleProvider(session=session, as_of=date.today())
        weeks = schedule.get_season_week_dates(season)

        if not weeks:
            print(f"  {season}: no weeks found, skipping")
            return []

        weeks = weeks[self.config.skip_first_weeks:]
        total_weeks = len(weeks)
        transactions = []

        for i, (monday, sunday) in enumerate(weeks):
            ctx = build_context(session, as_of=monday)

            all_players = ctx.stats.get_trailing_rankings(
                lookback_days=30, min_gp=5
            )
            rostered = all_players[: self.config.n_rostered]
            rostered_ids = {p["nhl_id"] for p in rostered}

            recent_players = ctx.stats.get_trailing_rankings(
                lookback_days=14, min_gp=1
            )
            fa_pool = [
                p for p in recent_players if p["nhl_id"] not in rostered_ids
            ]

            repl = ctx.stats.get_replacement_level(
                n_rostered=self.config.n_rostered
            )

            if i % 5 == 0:
                print(
                    f"  {season} week {i + 1}/{total_weeks} "
                    f"(pool={len(fa_pool)}, repl={repl['replacement_fpts_per_gp']:.2f})"
                )

            picks = self.config.strategy.decide(
                ctx=ctx,
                roster_nhl_ids=rostered_ids,
                fa_pool=fa_pool,
                adds_remaining=self.config.adds_per_week,
                aggression=AggressionLevel.NORMAL,
            )

            for pick in picks:
                transactions.append(
                    SimulationTransaction(
                        season=season,
                        week_num=i + 1,
                        pickup_date=monday,
                        week_start=monday,
                        week_end=sunday,
                        nhl_id=pick["nhl_id"],
                        name=pick["name"],
                        position=pick["position"],
                        fpts_per_gp_at_pickup=pick["fpts_per_gp"],
                        gp_at_pickup=pick.get("gp", 0),
                        replacement_fpts_per_gp=repl["replacement_fpts_per_gp"],
                        pool_size=len(fa_pool),
                    )
                )

        print(f"  {season}: {len(transactions)} transactions across {total_weeks} weeks")
        return transactions

    def _evaluate_transactions(
        self,
        session: Session,
        transactions: list[SimulationTransaction],
    ) -> None:
        """Score each transaction against actual outcomes in-place."""
        stats = StatsProvider(session=session, as_of=date.max)

        for i, txn in enumerate(transactions):
            weekly = stats.get_actual_fpts_in_range(
                txn.nhl_id, txn.week_start, txn.week_end
            )
            txn.actual_weekly_fpts = weekly["total_fpts"]
            txn.actual_weekly_gp = weekly["gp"]

            weekly_component = (
                txn.actual_weekly_fpts
                - txn.replacement_fpts_per_gp * txn.actual_weekly_gp
            )
            txn.weekly_component = round(weekly_component, 2)

            forward_end = txn.pickup_date + timedelta(days=29)
            forward = stats.get_actual_fpts_in_range(
                txn.nhl_id, txn.pickup_date, forward_end
            )
            if forward["gp"] >= 1:
                txn.actual_forward_fpts_per_gp = round(
                    forward["total_fpts"] / forward["gp"], 2
                )
                txn.actual_forward_gp = forward["gp"]
                txn.low_confidence = forward["gp"] < 5
            else:
                txn.actual_forward_fpts_per_gp = 0.0
                txn.actual_forward_gp = 0
                txn.low_confidence = True

            forward_component = (
                txn.actual_forward_fpts_per_gp - txn.replacement_fpts_per_gp
            )
            txn.forward_component = round(forward_component, 2)

            alpha = self.config.alpha
            txn.pickup_value = round(
                alpha * weekly_component + (1 - alpha) * forward_component, 2
            )

            if (i + 1) % 50 == 0:
                print(f"  Evaluated {i + 1}/{len(transactions)}")

    def _build_report(
        self,
        transactions: list[SimulationTransaction],
    ) -> SimulationReport:
        pickup_values = np.array([t.pickup_value for t in transactions])
        weekly_components = np.array([t.weekly_component for t in transactions])
        forward_components = np.array([t.forward_component for t in transactions])

        n = len(pickup_values)
        mean_pv = float(np.mean(pickup_values))
        std_pv = float(np.std(pickup_values, ddof=1)) if n > 1 else 0.0
        ci_margin = 1.96 * std_pv / math.sqrt(n) if n > 0 else 0.0

        per_season: dict[str, dict] = {}
        for season in self.config.seasons:
            s_txns = [t for t in transactions if t.season == season]
            if s_txns:
                s_vals = np.array([t.pickup_value for t in s_txns])
                per_season[season] = {
                    "n": len(s_txns),
                    "mean_pickup_value": round(float(np.mean(s_vals)), 2),
                    "hit_rate": round(float(np.mean(s_vals > 0)), 3),
                }

        return SimulationReport(
            strategy_name=self.config.strategy.name,
            seasons=self.config.seasons,
            n_rostered=self.config.n_rostered,
            alpha=self.config.alpha,
            transactions=transactions,
            n_transactions=n,
            mean_pickup_value=round(mean_pv, 2),
            pickup_value_ci_95=(
                round(mean_pv - ci_margin, 2),
                round(mean_pv + ci_margin, 2),
            ),
            hit_rate=round(float(np.mean(pickup_values > 0)), 3),
            mean_weekly_component=round(float(np.mean(weekly_components)), 2),
            mean_forward_component=round(float(np.mean(forward_components)), 2),
            per_season=per_season,
        )

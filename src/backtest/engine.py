"""Walk-forward transaction backtest engine using Yahoo league data.

For each fantasy week, iterates day-by-day (Mon-Sun):
1. Reconstruct roster from Yahoo as-of Monday, then track state
2. Each day: rebuild context with as_of=day, get fresh FA pool
3. Call strategy.decide() — take at most 1 swap per day
4. Apply swap to roster state so tomorrow sees post-swap roster
5. Score each transaction from its decided_on day forward
6. Compare agent picks against user's actual Yahoo transactions

Requires Yahoo transaction history to be synced for the league.
For backtesting without Yahoo data, use SimulationEngine instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from src.backtest.data_context import BacktestDataContext, build_context
from src.backtest.providers.fa_pool import FAPoolReconstructor
from src.core.queries.stats import StatsProvider
from src.backtest.report import (
    BacktestReport,
    EvaluatedTransaction,
    WeekResult,
)
from src.backtest.strategies import TransactionStrategy, BaselineStrategy
from src.core.db import get_session
from src.optimize.models import RosterSlotSettings
from src.optimize.models import AggressionLevel


@dataclass
class BacktestConfig:
    league_key: str = "465.l.17649"
    team_name: str = "McChuckin'"
    start_week: int = 5
    end_week: int = 20
    strategy: TransactionStrategy = field(default_factory=BaselineStrategy)
    aggression: AggressionLevel = AggressionLevel.NORMAL
    adds_per_week: int = 4
    fa_pool_size: int = 50
    waiver_days: int = 2
    roster_settings: RosterSlotSettings = field(default_factory=RosterSlotSettings)


class BacktestEngine:
    """Walk-forward backtest using real Yahoo league data."""

    def __init__(self, config: BacktestConfig):
        self.config = config

    def run(self) -> BacktestReport:
        weekly_results: list[WeekResult] = []

        with get_session() as session:
            fa_pool_recon = FAPoolReconstructor(
                session, self.config.league_key,
                waiver_days=self.config.waiver_days,
            )
            for week in range(self.config.start_week, self.config.end_week + 1):
                print(f"Backtesting week {week}...")
                result = self._run_week(session, week, fa_pool_recon)
                if result:
                    weekly_results.append(result)
                    n_agent = len(result.agent_transactions)
                    n_user = len(result.user_transactions)
                    days_used = {
                        t.decided_on.strftime("%a")
                        for t in result.agent_transactions
                    }
                    days_str = ",".join(sorted(days_used)) if days_used else "-"
                    print(
                        f"  Agent: {n_agent} adds ({days_str}), "
                        f"net {result.agent_net_fpts:+.1f} | "
                        f"User: {n_user} adds, net {result.user_net_fpts:+.1f} | "
                        f"Edge: {result.edge:+.1f}"
                    )

        report = self._build_report(weekly_results)
        print("\n" + report.summary())
        return report

    def _run_week(
        self,
        session: Session,
        yahoo_week: int,
        fa_pool_recon: FAPoolReconstructor,
    ) -> WeekResult | None:
        ctx = build_context(
            session,
            as_of=date.today(),
            league_key=self.config.league_key,
            team_name=self.config.team_name,
        )

        week_range = ctx.schedule.get_week_date_range(yahoo_week)
        if not week_range:
            print(f"  No games in week {yahoo_week}, skipping")
            return None

        monday, sunday = week_range

        monday_ctx = build_context(
            session,
            as_of=monday,
            league_key=self.config.league_key,
            team_name=self.config.team_name,
        )

        if not monday_ctx.yahoo:
            raise ValueError("BacktestEngine requires Yahoo league data")

        yahoo_roster = monday_ctx.yahoo.get_roster()
        roster_nhl_ids = {
            p["nhl_id"] for p in yahoo_roster if p.get("nhl_id")
        }

        if not roster_nhl_ids:
            print(f"  Empty roster for week {yahoo_week}, skipping")
            return None

        repl = monday_ctx.stats.get_replacement_level()
        outcome_stats = StatsProvider(session=session, as_of=date.max)

        game_days = self._count_game_days(session, monday, sunday)

        agent_txns: list[EvaluatedTransaction] = []
        adds_remaining = self.config.adds_per_week

        for day_offset in range(7):
            day = monday + timedelta(days=day_offset)
            if adds_remaining <= 0:
                break

            day_ctx = build_context(
                session,
                as_of=day,
                league_key=self.config.league_key,
                team_name=self.config.team_name,
            )

            league_unavailable = fa_pool_recon.get_unavailable_nhl_ids(day)
            unavailable = league_unavailable | roster_nhl_ids
            fa_pool = day_ctx.stats.get_trailing_rankings(
                lookback_days=30, min_gp=3,
            )
            fa_pool = [
                p for p in fa_pool if p["nhl_id"] not in unavailable
            ]
            fa_pool = fa_pool[: self.config.fa_pool_size]

            if not fa_pool:
                continue

            picks = self.config.strategy.decide(
                ctx=day_ctx,
                roster_nhl_ids=roster_nhl_ids,
                fa_pool=fa_pool,
                adds_remaining=adds_remaining,
                aggression=self.config.aggression,
            )

            if not picks:
                continue

            for pick in picks[:adds_remaining]:
                drop_id = pick.get("drop_nhl_id")
                if drop_id:
                    roster_nhl_ids.discard(drop_id)
                roster_nhl_ids.add(pick["nhl_id"])
                adds_remaining -= 1

                weekly = outcome_stats.get_actual_fpts_in_range(
                    pick["nhl_id"], day, sunday,
                )
                forward_end = day + timedelta(days=29)
                forward = outcome_stats.get_actual_fpts_in_range(
                    pick["nhl_id"], day, forward_end,
                )

                actual_weekly_fpts = weekly["total_fpts"]
                actual_forward_fpts_per_gp = (
                    forward["total_fpts"] / forward["gp"]
                    if forward["gp"] > 0 else 0.0
                )

                alpha = 0.5
                weekly_component = (
                    actual_weekly_fpts
                    - repl["replacement_fpts_per_gp"] * weekly["gp"]
                )
                forward_component = (
                    actual_forward_fpts_per_gp
                    - repl["replacement_fpts_per_gp"]
                )
                pickup_value = (
                    alpha * weekly_component
                    + (1 - alpha) * forward_component
                )

                agent_txns.append(EvaluatedTransaction(
                    decided_on=day,
                    add_nhl_id=pick["nhl_id"],
                    add_name=pick["name"],
                    drop_nhl_id=drop_id,
                    drop_name=pick.get("drop_name"),
                    projected_fpts_per_gp=pick.get("fpts_per_gp", 0),
                    projected_weekly_fpts=pick.get("weekly_fpts", 0),
                    actual_weekly_fpts=actual_weekly_fpts,
                    actual_weekly_gp=weekly["gp"],
                    actual_forward_fpts_per_gp=round(
                        actual_forward_fpts_per_gp, 2
                    ),
                    actual_forward_gp=forward["gp"],
                    pickup_value=round(pickup_value, 2),
                    weekly_component=round(weekly_component, 2),
                    forward_component=round(forward_component, 2),
                    reasoning=[pick.get("reasoning", "")],
                ))

        your_adds, your_drops = monday_ctx.yahoo.get_transactions_in_range(
            monday, sunday,
        )

        user_txns: list[EvaluatedTransaction] = []
        user_net = 0.0
        for add in your_adds:
            nhl_id = add.get("nhl_id")
            if not nhl_id:
                continue
            tx_date = add.get("date", monday)
            weekly = outcome_stats.get_actual_fpts_in_range(
                nhl_id, monday, sunday,
            )
            user_txns.append(EvaluatedTransaction(
                decided_on=tx_date if isinstance(tx_date, date) else monday,
                add_nhl_id=nhl_id,
                add_name=add["player_name"],
                actual_weekly_fpts=weekly["total_fpts"],
                actual_weekly_gp=weekly["gp"],
            ))
            user_net += weekly["total_fpts"]

        agent_net = sum(t.actual_weekly_fpts for t in agent_txns)

        return WeekResult(
            yahoo_week=yahoo_week,
            week_start=monday,
            agent_transactions=agent_txns,
            user_transactions=user_txns,
            agent_net_fpts=round(agent_net, 2),
            user_net_fpts=round(user_net, 2),
            edge=round(agent_net - user_net, 2),
            pool_size=len(fa_pool) if agent_txns else 0,
            replacement_fpts_per_gp=repl["replacement_fpts_per_gp"],
            game_days=game_days,
            adds_skipped=self.config.adds_per_week - len(agent_txns),
        )

    def _count_game_days(
        self, session: Session, start: date, end: date,
    ) -> int:
        """Count distinct days with NHL games in a date range."""
        from sqlalchemy import text

        row = session.execute(
            text("""
                SELECT COUNT(DISTINCT date) FROM games
                WHERE date >= :start AND date <= :end
                  AND home_score IS NOT NULL
            """),
            {"start": start, "end": end},
        ).scalar()
        return row or 0

    def _build_report(self, weekly_results: list[WeekResult]) -> BacktestReport:
        all_pickup_values = []
        for wr in weekly_results:
            for txn in wr.agent_transactions:
                all_pickup_values.append(txn.pickup_value)

        n = len(all_pickup_values)
        if n > 0:
            mean_pv = sum(all_pickup_values) / n
            variance = (
                sum((v - mean_pv) ** 2 for v in all_pickup_values) / (n - 1)
                if n > 1 else 0.0
            )
            std = math.sqrt(variance)
            ci_margin = 1.96 * std / math.sqrt(n)
            hit_rate = sum(1 for v in all_pickup_values if v > 0) / n
        else:
            mean_pv = 0.0
            ci_margin = 0.0
            hit_rate = 0.0

        day_dist: dict[str, int] = {}
        for wr in weekly_results:
            for txn in wr.agent_transactions:
                day_name = txn.decided_on.strftime("%a")
                day_dist[day_name] = day_dist.get(day_name, 0) + 1

        total_game_days = sum(wr.game_days for wr in weekly_results)
        idle_day_rate = (
            (total_game_days - n) / total_game_days
            if total_game_days > 0 else 0.0
        )

        return BacktestReport(
            strategy_name=self.config.strategy.name,
            start_week=self.config.start_week,
            end_week=self.config.end_week,
            weekly_results=weekly_results,
            total_agent_fpts=sum(wr.agent_net_fpts for wr in weekly_results),
            total_user_fpts=sum(wr.user_net_fpts for wr in weekly_results),
            total_edge=sum(wr.edge for wr in weekly_results),
            agent_total_adds=sum(
                len(wr.agent_transactions) for wr in weekly_results
            ),
            user_total_adds=sum(
                len(wr.user_transactions) for wr in weekly_results
            ),
            mean_pickup_value=round(mean_pv, 2),
            pickup_value_ci_95=(
                round(mean_pv - ci_margin, 2),
                round(mean_pv + ci_margin, 2),
            ),
            hit_rate=round(hit_rate, 3),
            day_of_week_distribution=day_dist,
            idle_day_rate=round(idle_day_rate, 3),
            total_adds_skipped=sum(wr.adds_skipped for wr in weekly_results),
        )

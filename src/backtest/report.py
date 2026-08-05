"""Report data models for backtest and simulation results.

Structured output that supports CLI printing, JSON export,
pandas DataFrame conversion, and JSONL persistence for
downstream hyperparameter sweeps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
import math
from pathlib import Path
from typing import Optional

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class EvaluatedTransaction:
    """A single transaction with full evaluation against actual outcomes."""

    decided_on: date
    add_nhl_id: int
    add_name: str
    drop_nhl_id: int | None = None
    drop_name: str | None = None

    projected_fpts_per_gp: float = 0.0
    projected_weekly_fpts: float = 0.0

    actual_weekly_fpts: float = 0.0
    actual_weekly_gp: int = 0
    actual_forward_fpts_per_gp: float = 0.0
    actual_forward_gp: int = 0

    pickup_value: float = 0.0
    weekly_component: float = 0.0
    forward_component: float = 0.0
    percentile_rank: float | None = None

    forecast_error: float | None = None

    reasoning: list[str] = field(default_factory=list)


@dataclass
class WeekResult:
    """Results for a single backtest week."""

    yahoo_week: int
    week_start: date

    agent_transactions: list[EvaluatedTransaction] = field(default_factory=list)
    user_transactions: list[EvaluatedTransaction] = field(default_factory=list)

    agent_net_fpts: float = 0.0
    user_net_fpts: float = 0.0
    edge: float = 0.0
    pool_size: int = 0
    replacement_fpts_per_gp: float = 0.0

    game_days: int = 0
    adds_skipped: int = 0


@dataclass
class ForecastAccuracyMetrics:
    """Forecast model evaluation metrics."""

    fpts_mae: float = 0.0
    fpts_bias: float = 0.0
    fpts_correlation: float = 0.0
    per_stat_mae: dict[str, float] = field(default_factory=dict)
    n_predictions: int = 0


@dataclass
class BacktestReport:
    """Full backtest report with decision quality and forecast accuracy."""

    strategy_name: str
    start_week: int
    end_week: int

    weekly_results: list[WeekResult] = field(default_factory=list)

    total_agent_fpts: float = 0.0
    total_user_fpts: float = 0.0
    total_edge: float = 0.0
    agent_total_adds: int = 0
    user_total_adds: int = 0

    mean_pickup_value: float = 0.0
    pickup_value_ci_95: tuple[float, float] = (0.0, 0.0)
    hit_rate: float = 0.0
    mean_percentile: float | None = None

    day_of_week_distribution: dict[str, int] = field(default_factory=dict)
    idle_day_rate: float = 0.0
    total_adds_skipped: int = 0

    forecast_accuracy: ForecastAccuracyMetrics | None = None

    def summary(self) -> str:
        lines = [
            f"Backtest: {self.strategy_name} | weeks {self.start_week}-{self.end_week}",
            f"",
            f"  Agent adds: {self.agent_total_adds}",
            f"  User adds:  {self.user_total_adds}",
            f"  Adds skipped: {self.total_adds_skipped}",
            f"",
            f"  Agent net FPTS: {self.total_agent_fpts:+.1f}",
            f"  User net FPTS:  {self.total_user_fpts:+.1f}",
            f"  Total edge:     {self.total_edge:+.1f}",
            f"",
            f"  Mean pickup value: {self.mean_pickup_value:+.2f}  "
            f"(95% CI: [{self.pickup_value_ci_95[0]:+.2f}, {self.pickup_value_ci_95[1]:+.2f}])",
            f"  Hit rate: {self.hit_rate:.1%}",
        ]

        if self.mean_percentile is not None:
            lines.append(f"  Mean percentile: {self.mean_percentile:.1%}")

        if self.day_of_week_distribution:
            dist_parts = []
            for day in _DAY_NAMES:
                count = self.day_of_week_distribution.get(day, 0)
                if count > 0:
                    dist_parts.append(f"{day}={count}")
            lines.append(f"  Day-of-week: {', '.join(dist_parts)}")

        total_game_days = sum(wr.game_days for wr in self.weekly_results)
        if total_game_days > 0:
            lines.append(
                f"  Idle-day rate: {self.idle_day_rate:.1%} "
                f"({total_game_days - self.agent_total_adds}/{total_game_days} game days)"
            )

        if self.forecast_accuracy:
            fa = self.forecast_accuracy
            lines.extend([
                f"",
                f"  Forecast accuracy ({fa.n_predictions} predictions):",
                f"    FPTS MAE:  {fa.fpts_mae:.3f}",
                f"    FPTS bias: {fa.fpts_bias:+.3f}",
                f"    FPTS corr: {fa.fpts_correlation:.3f}",
            ])

        lines.append("")
        lines.append("Weekly breakdown:")
        for wr in self.weekly_results:
            n_agent = len(wr.agent_transactions)
            n_user = len(wr.user_transactions)
            agent_days = ", ".join(
                t.decided_on.strftime("%a") for t in wr.agent_transactions
            ) or "-"
            lines.append(
                f"  Week {wr.yahoo_week}: "
                f"agent={wr.agent_net_fpts:+.1f} ({n_agent} adds: {agent_days}), "
                f"user={wr.user_net_fpts:+.1f} ({n_user} adds), "
                f"edge={wr.edge:+.1f}"
            )
            for txn in wr.agent_transactions:
                drop_str = f" (drop {txn.drop_name})" if txn.drop_name else ""
                lines.append(
                    f"    {txn.decided_on.strftime('%a %m/%d')}: "
                    f"+{txn.add_name}{drop_str} → "
                    f"{txn.actual_weekly_fpts:.1f} FPTS "
                    f"({txn.actual_weekly_gp}GP), "
                    f"pv={txn.pickup_value:+.2f}"
                )

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "start_week": self.start_week,
            "end_week": self.end_week,
            "total_agent_fpts": self.total_agent_fpts,
            "total_user_fpts": self.total_user_fpts,
            "total_edge": self.total_edge,
            "agent_total_adds": self.agent_total_adds,
            "user_total_adds": self.user_total_adds,
            "mean_pickup_value": self.mean_pickup_value,
            "pickup_value_ci_95": list(self.pickup_value_ci_95),
            "hit_rate": self.hit_rate,
            "mean_percentile": self.mean_percentile,
            "day_of_week_distribution": self.day_of_week_distribution,
            "idle_day_rate": self.idle_day_rate,
            "total_adds_skipped": self.total_adds_skipped,
            "weekly_results": [
                {
                    "yahoo_week": wr.yahoo_week,
                    "week_start": str(wr.week_start),
                    "agent_net_fpts": wr.agent_net_fpts,
                    "user_net_fpts": wr.user_net_fpts,
                    "edge": wr.edge,
                    "n_agent_adds": len(wr.agent_transactions),
                    "n_user_adds": len(wr.user_transactions),
                    "pool_size": wr.pool_size,
                    "replacement_fpts_per_gp": wr.replacement_fpts_per_gp,
                    "game_days": wr.game_days,
                    "adds_skipped": wr.adds_skipped,
                }
                for wr in self.weekly_results
            ],
        }

    def to_dataframe(self):
        """Convert weekly results to a pandas DataFrame for notebook analysis."""
        import pandas as pd

        rows = []
        for wr in self.weekly_results:
            rows.append({
                "yahoo_week": wr.yahoo_week,
                "week_start": wr.week_start,
                "agent_net_fpts": wr.agent_net_fpts,
                "user_net_fpts": wr.user_net_fpts,
                "edge": wr.edge,
                "n_agent_adds": len(wr.agent_transactions),
                "n_user_adds": len(wr.user_transactions),
                "pool_size": wr.pool_size,
                "replacement_fpts_per_gp": wr.replacement_fpts_per_gp,
                "game_days": wr.game_days,
                "adds_skipped": wr.adds_skipped,
                "cumulative_edge": 0.0,
            })

        df = pd.DataFrame(rows)
        if len(df) > 0:
            df["cumulative_edge"] = df["edge"].cumsum()
        return df

    def transactions_dataframe(self):
        """Convert all agent transactions to a DataFrame for deep analysis."""
        import pandas as pd

        rows = []
        for wr in self.weekly_results:
            for txn in wr.agent_transactions:
                rows.append({
                    "yahoo_week": wr.yahoo_week,
                    "decided_on": txn.decided_on,
                    "day_of_week": txn.decided_on.strftime("%a"),
                    "add_name": txn.add_name,
                    "add_nhl_id": txn.add_nhl_id,
                    "drop_name": txn.drop_name,
                    "drop_nhl_id": txn.drop_nhl_id,
                    "projected_fpts_per_gp": txn.projected_fpts_per_gp,
                    "projected_weekly_fpts": txn.projected_weekly_fpts,
                    "actual_weekly_fpts": txn.actual_weekly_fpts,
                    "actual_weekly_gp": txn.actual_weekly_gp,
                    "actual_forward_fpts_per_gp": txn.actual_forward_fpts_per_gp,
                    "actual_forward_gp": txn.actual_forward_gp,
                    "pickup_value": txn.pickup_value,
                    "weekly_component": txn.weekly_component,
                    "forward_component": txn.forward_component,
                    "percentile_rank": txn.percentile_rank,
                    "forecast_error": txn.forecast_error,
                    "reasoning": "; ".join(txn.reasoning),
                })

        return pd.DataFrame(rows)

    def to_jsonl(self, path: str | Path) -> int:
        """Write all transactions to a JSONL file for downstream sweeps.

        Returns number of records written.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        n = 0
        with open(path, "w") as f:
            for wr in self.weekly_results:
                for txn in wr.agent_transactions:
                    record = {
                        "strategy": self.strategy_name,
                        "yahoo_week": wr.yahoo_week,
                        "week_start": str(wr.week_start),
                        "decided_on": str(txn.decided_on),
                        "day_of_week": txn.decided_on.strftime("%a"),
                        "add_nhl_id": txn.add_nhl_id,
                        "add_name": txn.add_name,
                        "drop_nhl_id": txn.drop_nhl_id,
                        "drop_name": txn.drop_name,
                        "projected_fpts_per_gp": txn.projected_fpts_per_gp,
                        "projected_weekly_fpts": txn.projected_weekly_fpts,
                        "actual_weekly_fpts": txn.actual_weekly_fpts,
                        "actual_weekly_gp": txn.actual_weekly_gp,
                        "actual_forward_fpts_per_gp": txn.actual_forward_fpts_per_gp,
                        "actual_forward_gp": txn.actual_forward_gp,
                        "pickup_value": txn.pickup_value,
                        "weekly_component": txn.weekly_component,
                        "forward_component": txn.forward_component,
                        "replacement_fpts_per_gp": wr.replacement_fpts_per_gp,
                        "pool_size": wr.pool_size,
                        "reasoning": "; ".join(txn.reasoning),
                    }
                    f.write(json.dumps(record) + "\n")
                    n += 1

        return n


@dataclass
class SimulationTransaction:
    """A single simulated FA pickup with evaluation."""

    season: str
    week_num: int
    pickup_date: date
    week_start: date
    week_end: date

    nhl_id: int
    name: str
    position: str

    fpts_per_gp_at_pickup: float
    gp_at_pickup: int
    replacement_fpts_per_gp: float
    pool_size: int

    actual_weekly_fpts: float = 0.0
    actual_weekly_gp: int = 0
    actual_forward_fpts_per_gp: float = 0.0
    actual_forward_gp: int = 0

    pickup_value: float = 0.0
    weekly_component: float = 0.0
    forward_component: float = 0.0
    low_confidence: bool = False


@dataclass
class SimulationReport:
    """Full multi-season simulation report."""

    strategy_name: str
    seasons: list[str]
    n_rostered: int
    alpha: float

    transactions: list[SimulationTransaction] = field(default_factory=list)

    n_transactions: int = 0
    mean_pickup_value: float = 0.0
    pickup_value_ci_95: tuple[float, float] = (0.0, 0.0)
    hit_rate: float = 0.0
    mean_weekly_component: float = 0.0
    mean_forward_component: float = 0.0

    per_season: dict[str, dict] = field(default_factory=dict)

    def summary(self) -> str:
        ci = self.pickup_value_ci_95
        lines = [
            "=" * 60,
            f"FA POOL SIMULATION: {self.strategy_name}",
            "=" * 60,
            f"Seasons: {', '.join(self.seasons)}",
            f"Transactions: {self.n_transactions}",
            f"Alpha: {self.alpha}",
            f"",
            f"Mean pickup value: {self.mean_pickup_value:+.2f}  "
            f"(95% CI: [{ci[0]:+.2f}, {ci[1]:+.2f}])",
            f"Hit rate: {self.hit_rate:.1%}",
            f"Mean weekly component: {self.mean_weekly_component:+.2f}",
            f"Mean forward component: {self.mean_forward_component:+.2f}",
        ]

        if self.per_season:
            lines.append("\nPer-season:")
            for season, s in self.per_season.items():
                lines.append(
                    f"  {season}: n={s['n']:3d}  "
                    f"pv={s['mean_pickup_value']:+.2f}  "
                    f"hit={s['hit_rate']:.1%}"
                )

        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "seasons": self.seasons,
            "n_rostered": self.n_rostered,
            "alpha": self.alpha,
            "n_transactions": self.n_transactions,
            "mean_pickup_value": self.mean_pickup_value,
            "pickup_value_ci_95": list(self.pickup_value_ci_95),
            "hit_rate": self.hit_rate,
            "mean_weekly_component": self.mean_weekly_component,
            "mean_forward_component": self.mean_forward_component,
            "per_season": self.per_season,
        }

    def to_dataframe(self):
        """Convert all transactions to a DataFrame."""
        import pandas as pd

        rows = []
        for txn in self.transactions:
            rows.append({
                "season": txn.season,
                "week_num": txn.week_num,
                "pickup_date": txn.pickup_date,
                "name": txn.name,
                "position": txn.position,
                "nhl_id": txn.nhl_id,
                "fpts_per_gp_at_pickup": txn.fpts_per_gp_at_pickup,
                "gp_at_pickup": txn.gp_at_pickup,
                "replacement_fpts_per_gp": txn.replacement_fpts_per_gp,
                "pool_size": txn.pool_size,
                "actual_weekly_fpts": txn.actual_weekly_fpts,
                "actual_weekly_gp": txn.actual_weekly_gp,
                "actual_forward_fpts_per_gp": txn.actual_forward_fpts_per_gp,
                "actual_forward_gp": txn.actual_forward_gp,
                "pickup_value": txn.pickup_value,
                "weekly_component": txn.weekly_component,
                "forward_component": txn.forward_component,
                "low_confidence": txn.low_confidence,
            })

        return pd.DataFrame(rows)

"""Run a walk-forward transaction backtest against Yahoo league data.

Usage:
    python -m scripts.run_backtest --strategy baseline --weeks 10-15
    python -m scripts.run_backtest --strategy simple_value --weeks 5-20
    python -m scripts.run_backtest --strategy baseline --weeks 10-15 --output data/backtest.json
"""

import argparse
import json

from src.backtest.engine import BacktestEngine, BacktestConfig
from src.backtest.strategies import (
    BaselineStrategy,
    SimpleValueStrategy,
    ScheduleAwareStrategy,
    OracleStrategy,
    PuckAgentStrategy,
)
from src.optimize.models import AggressionLevel


STRATEGY_MAP = {
    "baseline": BaselineStrategy,
    "simple_value": SimpleValueStrategy,
    "schedule_aware": ScheduleAwareStrategy,
    "oracle": OracleStrategy,
    "puck_agent": PuckAgentStrategy,
}

AGGRESSION_MAP = {
    "conservative": AggressionLevel.CONSERVATIVE,
    "normal": AggressionLevel.NORMAL,
    "aggressive": AggressionLevel.AGGRESSIVE,
    "desperate": AggressionLevel.DESPERATE,
}


def main():
    parser = argparse.ArgumentParser(
        description="Run walk-forward transaction backtest"
    )
    parser.add_argument(
        "--strategy",
        choices=list(STRATEGY_MAP.keys()),
        default="baseline",
        help="Transaction strategy to evaluate (default: baseline)",
    )
    parser.add_argument(
        "--league",
        type=str,
        default="465.l.17649",
        help="Yahoo league key",
    )
    parser.add_argument(
        "--team",
        type=str,
        default="McChuckin'",
        help="Yahoo team name",
    )
    parser.add_argument(
        "--weeks",
        type=str,
        default="10-15",
        help="Week range like '10-15'",
    )
    parser.add_argument(
        "--aggression",
        choices=list(AGGRESSION_MAP.keys()),
        default="normal",
        help="Aggression level (default: normal)",
    )
    parser.add_argument(
        "--adds-per-week",
        type=int,
        default=4,
        help="Max adds per week (default: 4)",
    )
    parser.add_argument(
        "--fa-pool-size",
        type=int,
        default=50,
        help="Number of FAs to evaluate (default: 50)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to save JSON results",
    )

    args = parser.parse_args()

    start_week, end_week = [int(w) for w in args.weeks.split("-")]

    strategy_cls = STRATEGY_MAP[args.strategy]
    strategy = strategy_cls()

    config = BacktestConfig(
        league_key=args.league,
        team_name=args.team,
        start_week=start_week,
        end_week=end_week,
        strategy=strategy,
        aggression=AGGRESSION_MAP[args.aggression],
        adds_per_week=args.adds_per_week,
        fa_pool_size=args.fa_pool_size,
    )

    engine = BacktestEngine(config)
    report = engine.run()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report.to_dict(), f, indent=2, default=str)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()

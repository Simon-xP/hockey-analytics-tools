"""Run multi-season FA pool simulation with configurable strategy.

Usage:
    python -m scripts.run_simulation
    python -m scripts.run_simulation --strategy baseline --seasons 20232024 20242025 20252026
    python -m scripts.run_simulation --seasons 20252026 --n-rostered 160 --alpha 0.5
    python -m scripts.run_simulation --output data/sim_results.json
"""

import argparse
import json
import sys

from src.backtest.simulation import SimulationEngine, SimulationConfig
from src.backtest.strategies import BaselineStrategy


STRATEGY_MAP = {
    "baseline": BaselineStrategy,
}


def main():
    parser = argparse.ArgumentParser(
        description="Run multi-season FA pool simulation"
    )
    parser.add_argument(
        "--strategy",
        choices=list(STRATEGY_MAP.keys()),
        default="baseline",
        help="Transaction strategy to evaluate (default: baseline)",
    )
    parser.add_argument(
        "--seasons",
        nargs="+",
        default=None,
        help="Seasons to simulate (e.g., 20232024 20242025). Default: all available.",
    )
    parser.add_argument(
        "--n-rostered",
        type=int,
        default=160,
        help="Number of players considered 'rostered' (default: 160)",
    )
    parser.add_argument(
        "--adds-per-week",
        type=int,
        default=3,
        help="Max FA pickups per week (default: 3)",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.5,
        help="Weight for weekly vs forward component (default: 0.5)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to save JSON results",
    )

    args = parser.parse_args()

    strategy_cls = STRATEGY_MAP[args.strategy]
    strategy = strategy_cls()

    config = SimulationConfig(
        strategy=strategy,
        n_rostered=args.n_rostered,
        adds_per_week=args.adds_per_week,
        alpha=args.alpha,
    )

    if args.seasons:
        config.seasons = args.seasons

    engine = SimulationEngine(config)
    report = engine.run()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report.to_dict(), f, indent=2, default=str)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()

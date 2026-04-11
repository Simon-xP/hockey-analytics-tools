"""Train and evaluate the v2 forecasting model.

Trains situation-specific models (5v5, PP, PK, other) on historical data,
evaluates with walk-forward backtesting, and saves trained models.

Usage:
    # Default: train on 2024-25, test on 2025-26
    python -m scripts.train_forecasting_v2

    # Custom train/test
    python -m scripts.train_forecasting_v2 --train 20242025 --test 20252026

    # Train only, skip evaluation
    python -m scripts.train_forecasting_v2 --train 20242025 --no-eval
"""

import argparse

from src.tools.forecasting.v2.model import SituationModel
from src.tools.forecasting.v2.empirical_bayes import EmpiricalBayesPredictor
from src.tools.forecasting.v2.constants import SITUATION_CONFIGS


def main():
    parser = argparse.ArgumentParser(description="Train v2 forecasting models")
    parser.add_argument(
        "--train", nargs="+", default=["20242025"],
        help="Seasons for training",
    )
    parser.add_argument(
        "--test", nargs="+", default=["20252026"],
        help="Seasons for evaluation",
    )
    parser.add_argument(
        "--no-eval", action="store_true",
        help="Skip evaluation",
    )
    parser.add_argument(
        "--situations", nargs="+", default=None,
        help="Which situations to train (default: all)",
    )
    parser.add_argument(
        "--calibrate", nargs="+", default=None,
        help="Season(s) for post-training calibration (e.g., 20252026). "
             "Fits a linear correction to fix systematic prediction bias.",
    )
    args = parser.parse_args()

    situations = args.situations or list(SITUATION_CONFIGS.keys())

    print("=" * 60)
    print("FORECASTING MODEL v2 — TRAINING")
    print(f"Train seasons: {args.train}")
    if args.calibrate:
        print(f"Calibration seasons: {args.calibrate}")
    print(f"Situations: {situations}")
    print("=" * 60)

    for situation in situations:
        config = SITUATION_CONFIGS[situation]

        if situation in ("pk",):
            # PK: use Poisson for physical stats (shots, hits, blocks).
            # PK goals/assists use empirical Bayes (no model training needed).
            pk_physical_stats = [s for s in config["stats"] if s not in ("goals", "assists")]
            if pk_physical_stats:
                # Temporarily override config stats for PK physical model
                model = SituationModel(situation=situation)
                original_stats = config["stats"]
                SITUATION_CONFIGS[situation]["stats"] = pk_physical_stats
                result = model.train(train_seasons=args.train, use_poisson=True)
                SITUATION_CONFIGS[situation]["stats"] = original_stats
                if result:
                    model.save()

            print(f"\n  [{situation.upper()}] Goals/assists: using empirical Bayes "
                  f"(no XGBoost model — rates are too rare to predict per-game)")

        elif situation == "other":
            # Other: empirical Bayes for all stats (tiny TOI, all events rare)
            print(f"\n  [{situation.upper()}] All stats: using empirical Bayes "
                  f"(4v4/3v3/EN situations too rare for per-game prediction)")

        else:
            # 5v5 and PP: standard regression with TOI weighting
            model = SituationModel(situation=situation)
            result = model.train(train_seasons=args.train)

            if result:
                model.save()

                # Print feature importance for the primary stat
                primary_stat = config["stats"][0]
                print(f"\n  Feature importance ({situation} {primary_stat}):")
                for name, imp in model.feature_importance(primary_stat, top_n=10):
                    bar = "█" * int(imp * 100)
                    print(f"    {name:40s} {imp:.4f} {bar}")

    print("\n" + "=" * 60)
    print("TRAINING COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()

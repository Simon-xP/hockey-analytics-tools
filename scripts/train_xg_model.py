"""Train and evaluate the xG model.

Trains XGBoost classifiers per strength state on historical shot data,
evaluates on a holdout season, and saves the trained model.

Usage:
    # Train on 2019-2024, test on 2024-25
    python -m scripts.train_xg_model

    # Custom train/test split
    python -m scripts.train_xg_model \
        --train 20192020 20202021 20212022 20222023 20232024 \
        --test 20242025

    # Train only (no eval)
    python -m scripts.train_xg_model --train 20242025 --no-eval
"""

import argparse

from src.tools.xg.model import XGModel


DEFAULT_TRAIN_SEASONS = [
    "20212022", "20222023", "20232024", "20242025",
]
DEFAULT_TEST_SEASONS = ["20252026"]


def main():
    parser = argparse.ArgumentParser(description="Train xG model")
    parser.add_argument(
        "--train", nargs="+", default=DEFAULT_TRAIN_SEASONS,
        help="Seasons for training",
    )
    parser.add_argument(
        "--test", nargs="+", default=DEFAULT_TEST_SEASONS,
        help="Seasons for evaluation",
    )
    parser.add_argument(
        "--no-eval", action="store_true",
        help="Skip evaluation step",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output path for saved model (default: models/xg/xg_latest.pkl)",
    )
    args = parser.parse_args()

    model = XGModel()

    # Train
    print("=" * 60)
    print("TRAINING")
    print("=" * 60)
    train_results = model.train(train_seasons=args.train)

    if not train_results:
        print("Training failed — no data.")
        return

    # Evaluate
    if not args.no_eval:
        print()
        print("=" * 60)
        print("EVALUATION")
        print("=" * 60)
        eval_results = model.evaluate(test_seasons=args.test)

        if eval_results:
            print()
            print("=" * 60)
            print("FEATURE IMPORTANCE (5v5)")
            print("=" * 60)
            for name, importance in model.feature_importance("5v5"):
                bar = "█" * int(importance * 100)
                print(f"  {name:35s} {importance:.4f} {bar}")

    # Save
    print()
    path = model.save(args.output)
    print(f"Model saved to {path}")


if __name__ == "__main__":
    main()

"""Score shot attempts with the trained xG model.

Loads the trained xG model and writes predictions back to the shot_attempts
table. Only scores shots that don't already have an xG value.

Usage:
    # Score all unscored shots
    python -m scripts.score_xg

    # Score a specific season
    python -m scripts.score_xg --season 20252026

    # Re-score all shots (overwrite existing)
    python -m scripts.score_xg --force
"""

import argparse

import numpy as np
from sqlalchemy import text

from src.core.db import get_session
from src.tools.xg.model import XGModel, load_shot_data, build_feature_matrix, _classify_strength


def score_shots(
    seasons: list[str] | None = None,
    force: bool = False,
    model_path: str | None = None,
) -> dict:
    """Score shot attempts with xG predictions.

    Returns dict with counts.
    """
    model = XGModel.load(model_path)
    print(f"Loaded model with groups: {list(model.models.keys())}")

    # Load shots
    df = load_shot_data(seasons=seasons)
    total = len(df)

    if not force:
        # Only score unscored shots — but load_shot_data doesn't return xg column
        # So we check the DB for which shots already have xg
        with get_session() as session:
            if seasons:
                conditions = []
                params = {}
                for i, season in enumerate(seasons):
                    start_year = int(season[:4])
                    conditions.append(
                        f"(game_id >= :lo_{i} AND game_id < :hi_{i})"
                    )
                    params[f"lo_{i}"] = start_year * 1_000_000
                    params[f"hi_{i}"] = (start_year + 1) * 1_000_000
                where = f"WHERE ({' OR '.join(conditions)}) AND xg IS NOT NULL"
            else:
                where = "WHERE xg IS NOT NULL"
                params = {}

            scored_count = session.execute(
                text(f"SELECT COUNT(*) FROM shot_attempts {where}"), params
            ).scalar()

        if scored_count == total:
            print(f"All {total} shots already scored. Use --force to re-score.")
            return {"total": total, "scored": 0, "skipped": total}

    print(f"Scoring {total} shot attempts...")

    if total == 0:
        return {"total": 0, "scored": 0, "skipped": 0}

    # Predict
    xg_pred = model.predict_batch(df)

    # Write back to DB
    with get_session() as session:
        # Batch update using game_id + event_id
        batch_size = 1000
        updated = 0
        for i in range(0, len(df), batch_size):
            batch_df = df.iloc[i:i + batch_size]
            batch_xg = xg_pred[i:i + batch_size]

            for j, (_, row) in enumerate(batch_df.iterrows()):
                session.execute(
                    text(
                        "UPDATE shot_attempts SET xg = :xg "
                        "WHERE game_id = :game_id AND event_id = :event_id"
                    ),
                    {
                        "xg": float(batch_xg[j]),
                        "game_id": int(row["game_id"]),
                        "event_id": int(row["event_id"]),
                    },
                )
                updated += 1

            if (i + batch_size) % 10000 == 0 or i + batch_size >= len(df):
                print(f"  Updated {min(i + batch_size, len(df))}/{total}")

    print(f"Done: scored {updated} shots")
    print(f"  Mean xG: {xg_pred.mean():.4f}")
    print(f"  Actual goal rate: {df['is_goal'].mean():.4f}")

    return {"total": total, "scored": updated, "skipped": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Score shots with xG model")
    parser.add_argument(
        "--season", nargs="+", default=None,
        help="Season(s) to score",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-score all shots even if already scored",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Path to model file (default: models/xg/xg_latest.pkl)",
    )
    args = parser.parse_args()
    score_shots(seasons=args.season, force=args.force, model_path=args.model)

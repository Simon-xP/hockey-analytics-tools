"""Benchmark our xG model against MoneyPuck's published per-shot xG values.

Loads MoneyPuck's shot-level CSV and our shot_attempts table, matches shots
by game_id + shooter + period + time, and compares xG predictions head-to-head.

Usage:
    # Compare on 2024-25 season (default)
    python -m scripts.benchmark_xg

    # Use a different MoneyPuck CSV
    python -m scripts.benchmark_xg --csv data/moneypuck/shots_2023.csv

    # Compare on a subset
    python -m scripts.benchmark_xg --season 20242025
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss

from src.analytics.xg.model import XGModel, load_shot_data, _classify_strength

MP_CSV_DIR = Path("data/moneypuck")

# MoneyPuck game_id is short form: first game of 2024-25 = 20001
# NHL game_id is: 2024020001
# Conversion: nhl_game_id = (season_start_year * 1_000_000) + 20000 + mp_game_id_offset
# Actually: mp game_id for 2024-25 starts at 20001 for regular season game 1
# NHL game_id: 2024020001
# So: nhl_id = 2024000000 + mp_id  ... let's check: 2024000000 + 20001 = 2024020001 ✓

SEASON_YEAR_MAP = {
    "20252026": 2025,
    "20242025": 2024,
    "20232024": 2023,
    "20222023": 2022,
    "20212022": 2021,
    "20202021": 2020,
    "20192020": 2019,
    "20182019": 2018,
}


def mp_to_nhl_game_id(mp_game_id: int, season_start_year: int) -> int:
    """Convert MoneyPuck game_id to NHL game_id."""
    return season_start_year * 1_000_000 + mp_game_id


def load_moneypuck_data(csv_path: str, season_start_year: int) -> pd.DataFrame:
    """Load MoneyPuck shot data and add NHL game ID."""
    cols = [
        "game_id", "goal", "xGoal", "shotType", "shotDistance", "shotAngle",
        "arenaAdjustedXCord", "arenaAdjustedYCord", "arenaAdjustedShotDistance",
        "shotRebound", "shotRush", "period", "time",
        "homeSkatersOnIce", "awaySkatersOnIce",
        "event", "shooterPlayerId", "goalieIdForShot",
        "isPlayoffGame", "homeTeamCode", "awayTeamCode",
    ]
    df = pd.read_csv(csv_path, usecols=[c for c in cols if c in pd.read_csv(csv_path, nrows=0).columns])

    # Filter to regular season only
    if "isPlayoffGame" in df.columns:
        df = df[df["isPlayoffGame"] == 0].copy()

    # Convert game IDs
    df["nhl_game_id"] = df["game_id"].apply(
        lambda x: mp_to_nhl_game_id(x, season_start_year)
    )

    return df


def match_shots(our_df: pd.DataFrame, mp_df: pd.DataFrame) -> pd.DataFrame:
    """Match our shots with MoneyPuck shots by game + shooter + period + time.

    Returns merged DataFrame with both xG predictions.
    """
    # Prepare our data for merge
    cols = ["game_id", "shooter_id", "period", "game_seconds",
            "is_goal", "event_type"]
    if "our_xg" in our_df.columns:
        cols.append("our_xg")
    ours = our_df[cols].copy()
    ours = ours.rename(columns={"game_id": "nhl_game_id"})

    # Prepare MoneyPuck data
    mp = mp_df[["nhl_game_id", "shooterPlayerId", "period", "time",
                "goal", "xGoal", "event"]].copy()
    mp = mp.rename(columns={
        "shooterPlayerId": "shooter_id",
        "goal": "mp_goal",
        "xGoal": "mp_xg",
    })

    # Match on game + shooter + period (time may differ slightly, so we do
    # an approximate match within the same period)
    merged = pd.merge(
        ours, mp,
        on=["nhl_game_id", "shooter_id", "period"],
        how="inner",
    )

    # Filter to shots that match closely in time (within 5 seconds)
    # MoneyPuck "time" is seconds into the period
    # Our "game_seconds" is total game seconds, convert to period seconds
    merged["our_period_seconds"] = merged["game_seconds"] - (merged["period"] - 1) * 1200
    merged["time_diff"] = abs(merged["our_period_seconds"] - merged["time"])
    merged = merged[merged["time_diff"] <= 5]

    # Keep closest time match per our shot
    merged = merged.sort_values("time_diff").drop_duplicates(
        subset=["nhl_game_id", "shooter_id", "period", "game_seconds"],
        keep="first",
    )

    return merged


def benchmark(
    season: str = "20242025",
    csv_path: str | None = None,
    model_path: str | None = None,
) -> dict:
    """Run full benchmark: our model vs MoneyPuck on the same shots."""

    season_year = SEASON_YEAR_MAP.get(season)
    if season_year is None:
        print(f"Unknown season: {season}")
        return {}

    # Determine CSV path
    if csv_path is None:
        csv_path = MP_CSV_DIR / f"shots_{season_year}.csv"
    csv_path = Path(csv_path)

    if not csv_path.exists():
        print(f"MoneyPuck CSV not found: {csv_path}")
        print(f"Download from: https://peter-tanner.com/moneypuck/downloads/shots_{season_year}.zip")
        return {}

    # Load MoneyPuck data
    print(f"Loading MoneyPuck data from {csv_path}...")
    mp_df = load_moneypuck_data(str(csv_path), season_year)
    print(f"  {len(mp_df)} regular season shots, {mp_df['goal'].sum()} goals "
          f"({mp_df['goal'].mean()*100:.1f}%)")

    # Load our shot data
    print(f"Loading our shot data for {season}...")
    our_df = load_shot_data(seasons=[season])
    if len(our_df) == 0:
        print("  No shot data in our DB. Run build_shot_attempts first.")
        return {}
    print(f"  {len(our_df)} shots, {our_df['is_goal'].sum()} goals")

    # Load and score with our model
    model = XGModel.load(model_path)
    print(f"  Scoring with our model (groups: {list(model.models.keys())})...")
    our_df["our_xg"] = model.predict_batch(our_df)

    # Match shots
    print("Matching shots between datasets...")
    matched = match_shots(our_df, mp_df)
    print(f"  Matched {len(matched)} shots")

    if len(matched) < 100:
        print("  Too few matches for meaningful comparison.")
        return {}

    y_true = matched["is_goal"].astype(int).values
    our_xg = matched["our_xg"].values
    mp_xg = matched["mp_xg"].values

    # Compute metrics for both
    results = {}
    for name, preds in [("Ours", our_xg), ("MoneyPuck", mp_xg)]:
        auc = roc_auc_score(y_true, preds)
        ll = log_loss(y_true, preds)
        brier = brier_score_loss(y_true, preds)

        results[name] = {
            "auc": float(auc),
            "log_loss": float(ll),
            "brier": float(brier),
            "mean_xg": float(preds.mean()),
        }

    # Print comparison
    print()
    print("=" * 60)
    print(f"BENCHMARK RESULTS ({len(matched)} matched shots)")
    print("=" * 60)
    print(f"  Actual goal rate: {y_true.mean()*100:.2f}%")
    print()
    print(f"  {'Metric':<15s} {'Ours':>12s} {'MoneyPuck':>12s} {'Diff':>12s}")
    print(f"  {'-'*15} {'-'*12} {'-'*12} {'-'*12}")

    for metric in ["auc", "log_loss", "brier", "mean_xg"]:
        ours = results["Ours"][metric]
        mp = results["MoneyPuck"][metric]
        diff = ours - mp
        # For AUC, higher is better. For log_loss/brier, lower is better.
        better = "auc" if metric == "auc" or metric == "mean_xg" else "lower"
        sign = "+" if diff > 0 else ""
        print(f"  {metric:<15s} {ours:>12.4f} {mp:>12.4f} {sign}{diff:>11.4f}")

    # Correlation between our xG and MoneyPuck xG
    corr = np.corrcoef(our_xg, mp_xg)[0, 1]
    print(f"\n  Correlation (our xG vs MoneyPuck xG): {corr:.4f}")

    # Per-event-type comparison
    print(f"\n  Per event type:")
    for event_type in matched["event_type"].unique():
        mask = matched["event_type"] == event_type
        if mask.sum() < 50:
            continue
        our_mean = our_xg[mask].mean()
        mp_mean = mp_xg[mask].mean()
        actual = y_true[mask].mean()
        print(f"    {event_type:20s}: ours={our_mean:.4f} mp={mp_mean:.4f} "
              f"actual={actual:.4f} (n={mask.sum()})")

    results["n_matched"] = len(matched)
    results["correlation"] = float(corr)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark xG against MoneyPuck")
    parser.add_argument("--season", default="20242025", help="Season to compare")
    parser.add_argument("--csv", default=None, help="Path to MoneyPuck CSV")
    parser.add_argument("--model", default=None, help="Path to our model")
    args = parser.parse_args()

    benchmark(season=args.season, csv_path=args.csv, model_path=args.model)

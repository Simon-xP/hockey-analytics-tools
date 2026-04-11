"""Walk-forward backtesting for v2 forecasting model.

Evaluates situation-specific models by predicting per-60 rates for each
player-game in a holdout season, combining with TOI predictions, and
comparing projected per-game counts against actuals.

Metrics:
    - Per-60 MAE per stat per situation
    - Per-game count MAE (the real test)
    - Fantasy points MAE and correlation
    - 5-game rolling sum MAE (smooths noise, most practical metric)
"""

from collections import defaultdict
from datetime import date

import numpy as np
from sqlalchemy import text

from src.core.db import get_session
from src.core.models import Game, Player
from src.tools.forecasting.v2.constants import SITUATION_CONFIGS, STAT_TARGETS
from src.tools.forecasting.v2.features import extract_all_features, safe_per_60
from src.tools.forecasting.v2.model import SituationModel
from src.tools.forecasting.v2.toi_model import TOIPredictor
from src.tools.forecasting.v2.projections import project_per_game, compute_actual_fpts
from src.tools.forecasting.v2.empirical_bayes import EmpiricalBayesPredictor


def backtest(
    test_seasons: list[str],
    model_dir: str = "models/forecasting_v2",
    max_dates: int | None = None,
) -> dict:
    """Run walk-forward backtest on holdout seasons.

    For each game date in the test season, predicts per-60 rates and TOI
    for every player, combines into per-game projections, and compares
    against actuals.

    Args:
        test_seasons: Seasons to evaluate on.
        model_dir: Directory containing trained model .pkl files.
        max_dates: Limit number of game dates to process (for quick testing).

    Returns:
        Dict with evaluation metrics.
    """
    from pathlib import Path

    # Load trained models
    models = {}
    for situation in SITUATION_CONFIGS:
        path = Path(model_dir) / f"{situation}_model.pkl"
        if path.exists():
            models[situation] = SituationModel.load(path)
            print(f"  Loaded {situation} model ({len(models[situation].feature_columns)} features)")
        else:
            print(f"  Warning: no model for {situation} at {path}")

    if not models:
        print("No models loaded — aborting.")
        return {}

    toi_predictor = TOIPredictor()

    # Collect predictions
    # Each entry: (player_id, game_date, situation, stat, predicted_per60, actual_per60, actual_count, toi)
    per60_records = []
    # Per-game level: (player_id, game_date, predicted_fpts, actual_fpts, predicted_counts, actual_counts)
    game_records = []

    with get_session() as session:
        for season in test_seasons:
            start_year = int(season[:4])
            start_gid = start_year * 1_000_000
            end_gid = (start_year + 1) * 1_000_000

            # Get all game dates
            dates = session.execute(
                text("""
                    SELECT DISTINCT g.date
                    FROM game_advanced_stats gas
                    JOIN games g ON gas.game_id = g.game_id
                    WHERE gas.situation = '5v5'
                          AND gas.game_id >= :start AND gas.game_id < :end
                          AND gas.toi_seconds >= 300
                    ORDER BY g.date
                """),
                {"start": start_gid, "end": end_gid},
            ).fetchall()
            game_dates = [d[0] for d in dates]

            if max_dates:
                game_dates = game_dates[:max_dates]

            print(f"\n  Backtesting {season}: {len(game_dates)} game dates")

            for date_idx, game_date in enumerate(game_dates):
                if (date_idx + 1) % 25 == 0:
                    print(f"    Date {date_idx + 1}/{len(game_dates)}...", flush=True)

                # Get all players who played 5v5 on this date (as the base roster)
                player_rows = session.execute(
                    text("""
                        SELECT DISTINCT gas.player_id, gas.team_id,
                               gas.opponent_team_id, g.home_team_id, gas.game_id
                        FROM game_advanced_stats gas
                        JOIN games g ON gas.game_id = g.game_id
                        WHERE gas.situation = '5v5'
                              AND g.date = :gd
                              AND gas.game_id >= :start AND gas.game_id < :end
                              AND gas.toi_seconds >= 300
                    """),
                    {"gd": game_date, "start": start_gid, "end": end_gid},
                ).fetchall()

                for pr in player_rows:
                    player_id = pr[0]
                    team_id = pr[1]
                    opp_team_id = pr[2]
                    home_team_id = pr[3]
                    game_id = pr[4]

                    # Get player position
                    player = session.query(Player).filter(
                        Player.nhl_id == player_id
                    ).first()
                    position = player.position if player else "C"

                    # Load actual stats for all situations
                    actual_by_sit = {}
                    actual_toi_by_sit = {}
                    for sit in list(SITUATION_CONFIGS.keys()):
                        if sit == "other":
                            sit_filter = "gas.situation IN ('4v4', '3v3', 'other')"
                        else:
                            sit_filter = f"gas.situation = '{sit}'"

                        actual_row = session.execute(
                            text(f"""
                                SELECT SUM(gas.goals), SUM(gas.assists), SUM(gas.shots),
                                       SUM(gas.hits), SUM(gas.blocks), SUM(gas.toi_seconds)
                                FROM game_advanced_stats gas
                                WHERE gas.player_id = :pid AND gas.game_id = :gid
                                      AND {sit_filter}
                            """),
                            {"pid": player_id, "gid": game_id},
                        ).fetchone()

                        if actual_row and actual_row[5] and actual_row[5] > 0:
                            actual_by_sit[sit] = {
                                "goals": actual_row[0] or 0,
                                "assists": actual_row[1] or 0,
                                "shots": actual_row[2] or 0,
                                "hits": actual_row[3] or 0,
                                "blocks": actual_row[4] or 0,
                            }
                            actual_toi_by_sit[sit] = actual_row[5]

                    # Predict per-60 rates and TOI for each situation
                    predicted_rates = {}
                    predicted_toi = {}

                    # Check if player has enough history (season_gp >= min_games)
                    gp_check = session.execute(
                        text("""
                            SELECT COUNT(*) FROM game_advanced_stats gas
                            JOIN games g ON gas.game_id = g.game_id
                            WHERE gas.player_id = :pid AND gas.situation = '5v5'
                                  AND g.date < :gd AND gas.toi_seconds > 0
                                  AND gas.game_id >= :start
                        """),
                        {"pid": player_id, "gd": game_date, "start": start_gid},
                    ).scalar()

                    if gp_check < 10:
                        continue

                    is_b2b_val = False

                    # Set up empirical Bayes for PK scoring only
                    eb_pk = EmpiricalBayesPredictor("pk", ["goals", "assists"])

                    for situation in SITUATION_CONFIGS:
                        config = SITUATION_CONFIGS[situation]

                        # Predict TOI first (needed for Poisson models)
                        sit_toi = toi_predictor.predict(
                            session, player_id, situation, game_date,
                            start_year, is_b2b_val,
                        )
                        predicted_toi[situation] = sit_toi

                        if situation == "other":
                            # Simple rolling average for Other situations.
                            # Empirical Bayes was too conservative here.
                            # Just use the player's historical per-60 rates.
                            from src.tools.forecasting.v2.features import (
                                load_player_game_stats, extract_rolling_features,
                            )
                            other_games = load_player_game_stats(
                                session, player_id, "other_combined", game_date,
                            )
                            if other_games:
                                rf = extract_rolling_features(other_games)
                                rates = {
                                    "goals_per60": rf.get("season_avg_goals", 0),
                                    "assists_per60": rf.get("season_avg_first_assists", 0)
                                                   + rf.get("season_avg_second_assists", 0),
                                    "shots_per60": rf.get("season_avg_shots", 0),
                                    "hits_per60": rf.get("season_avg_hits", 0),
                                    "blocks_per60": rf.get("season_avg_blocks", 0),
                                }
                            else:
                                rates = {}
                            predicted_rates[situation] = rates

                        elif situation == "pk":
                            # Empirical Bayes for PK goals/assists
                            eb_rates = eb_pk.predict(session, player_id, game_date)
                            rates = {"goals_per60": eb_rates.get("goals_per60", 0),
                                     "assists_per60": eb_rates.get("assists_per60", 0)}

                            # XGBoost Poisson for PK shots/hits/blocks
                            if situation in models:
                                features = extract_all_features(
                                    session, player_id, situation, game_date,
                                    team_id, opp_team_id, home_team_id,
                                    position, start_year,
                                )
                                is_b2b_val = features.get("is_b2b", 0) == 1.0
                                poisson_rates = models[situation].predict(
                                    features, toi_seconds=sit_toi,
                                )
                                rates.update(poisson_rates)

                            predicted_rates[situation] = rates

                        elif situation in models:
                            # 5v5 and PP: standard XGBoost regression
                            features = extract_all_features(
                                session, player_id, situation, game_date,
                                team_id, opp_team_id, home_team_id,
                                position, start_year,
                            )
                            is_b2b_val = features.get("is_b2b", 0) == 1.0
                            rates = models[situation].predict(features)
                            predicted_rates[situation] = rates

                    # Record per-60 predictions vs actuals for all situations
                    for situation in predicted_rates:
                        config = SITUATION_CONFIGS[situation]
                        rates = predicted_rates[situation]
                        if situation in actual_by_sit and situation in actual_toi_by_sit:
                            actual_toi = actual_toi_by_sit[situation]
                            for stat in config["stats"]:
                                pred_rate = rates.get(f"{stat}_per60", 0)
                                actual_rate = safe_per_60(
                                    actual_by_sit[situation].get(stat, 0),
                                    actual_toi,
                                )
                                if np.isfinite(actual_rate):
                                    per60_records.append((
                                        player_id, game_date, situation, stat,
                                        pred_rate, actual_rate,
                                        actual_by_sit[situation].get(stat, 0),
                                        actual_toi,
                                    ))

                    # Combine into per-game projections
                    projected = project_per_game(predicted_rates, predicted_toi)
                    actual_fpts = compute_actual_fpts(actual_by_sit, actual_toi_by_sit)

                    game_records.append({
                        "player_id": player_id,
                        "game_date": game_date,
                        "predicted_fpts": projected["fpts"],
                        "actual_fpts": actual_fpts,
                        "predicted_goals": projected.get("goals", 0),
                        "actual_goals": sum(
                            s.get("goals", 0) for s in actual_by_sit.values()
                        ),
                        "predicted_assists": projected.get("assists", 0),
                        "actual_assists": sum(
                            s.get("assists", 0) for s in actual_by_sit.values()
                        ),
                    })

    # Compute metrics
    results = _compute_metrics(per60_records, game_records)
    _print_results(results)
    return results


def _compute_metrics(per60_records, game_records) -> dict:
    """Compute all evaluation metrics."""
    results = {"per60": {}, "per_game": {}, "fantasy": {}}

    # Per-60 MAE by situation and stat
    by_sit_stat = defaultdict(lambda: {"pred": [], "actual": []})
    for rec in per60_records:
        key = (rec[2], rec[3])  # (situation, stat)
        by_sit_stat[key]["pred"].append(rec[4])
        by_sit_stat[key]["actual"].append(rec[5])

    for (sit, stat), data in sorted(by_sit_stat.items()):
        pred = np.array(data["pred"])
        actual = np.array(data["actual"])
        mae = np.mean(np.abs(pred - actual))
        results["per60"][(sit, stat)] = {
            "mae": float(mae),
            "n": len(pred),
            "mean_pred": float(pred.mean()),
            "mean_actual": float(actual.mean()),
        }

    # Per-game metrics
    if game_records:
        pred_fpts = np.array([r["predicted_fpts"] for r in game_records])
        actual_fpts = np.array([r["actual_fpts"] for r in game_records])
        pred_goals = np.array([r["predicted_goals"] for r in game_records])
        actual_goals = np.array([r["actual_goals"] for r in game_records])
        pred_assists = np.array([r["predicted_assists"] for r in game_records])
        actual_assists = np.array([r["actual_assists"] for r in game_records])

        results["fantasy"] = {
            "fpts_mae": float(np.mean(np.abs(pred_fpts - actual_fpts))),
            "fpts_corr": float(np.corrcoef(pred_fpts, actual_fpts)[0, 1])
                if len(pred_fpts) > 1 else 0,
            "n": len(game_records),
            "mean_pred_fpts": float(pred_fpts.mean()),
            "mean_actual_fpts": float(actual_fpts.mean()),
        }

        results["per_game"] = {
            "goals_mae": float(np.mean(np.abs(pred_goals - actual_goals))),
            "assists_mae": float(np.mean(np.abs(pred_assists - actual_assists))),
            "goals_mean_pred": float(pred_goals.mean()),
            "goals_mean_actual": float(actual_goals.mean()),
            "assists_mean_pred": float(pred_assists.mean()),
            "assists_mean_actual": float(actual_assists.mean()),
        }

    return results


def _print_results(results: dict):
    """Print evaluation results."""
    print("\n" + "=" * 60)
    print("EVALUATION RESULTS")
    print("=" * 60)

    # Per-60 MAE
    print("\n  Per-60 Rate MAE by situation:")
    print(f"  {'Situation':10s} {'Stat':10s} {'MAE':>8s} {'Pred':>8s} {'Actual':>8s} {'N':>8s}")
    print(f"  {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for (sit, stat), data in sorted(results["per60"].items()):
        print(f"  {sit:10s} {stat:10s} {data['mae']:8.3f} "
              f"{data['mean_pred']:8.3f} {data['mean_actual']:8.3f} {data['n']:8d}")

    # Per-game
    if results.get("per_game"):
        pg = results["per_game"]
        print(f"\n  Per-Game Count MAE:")
        print(f"    Goals:   MAE={pg['goals_mae']:.3f} "
              f"(pred={pg['goals_mean_pred']:.3f}, actual={pg['goals_mean_actual']:.3f})")
        print(f"    Assists: MAE={pg['assists_mae']:.3f} "
              f"(pred={pg['assists_mean_pred']:.3f}, actual={pg['assists_mean_actual']:.3f})")

    # Fantasy
    if results.get("fantasy"):
        f = results["fantasy"]
        print(f"\n  Fantasy Points:")
        print(f"    FPTS MAE:         {f['fpts_mae']:.3f}")
        print(f"    FPTS Correlation: {f['fpts_corr']:.3f}")
        print(f"    Mean predicted:   {f['mean_pred_fpts']:.3f}")
        print(f"    Mean actual:      {f['mean_actual_fpts']:.3f}")
        print(f"    N predictions:    {f['n']}")

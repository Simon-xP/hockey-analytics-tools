"""Situation-specific forecasting models (v2).

Each SituationModel trains one XGBRegressor per stat for a specific game
situation (5v5, PP, PK, other). The model predicts per-60 rates using
features extracted from game_advanced_stats.

Training uses walk-forward protocol: for each game date in the training
season, extract features from all prior games only, then use the actual
per-60 rate as the target. No data leakage.
"""

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from sqlalchemy import text

from src.core.db import get_session
from src.core.models import Game, Player
from src.tools.forecasting.v2.constants import (
    SITUATION_CONFIGS, STAT_TARGETS, feature_allowed_for_stat,
)
from src.tools.forecasting.v2.features import (
    extract_all_features,
    safe_per_60,
    load_player_game_stats,
)

MODEL_DIR = Path("models/forecasting_v2")


@dataclass
class SituationModel:
    """Per-situation forecasting model.

    One XGBRegressor per stat. Predicts per-60 rates for a given situation.
    Optional post-training calibration corrects systematic bias (especially
    useful for PK/Other where per-60 rates are noisy).
    """
    situation: str
    models: dict = field(default_factory=dict)  # stat_name -> fitted XGBRegressor
    feature_columns: list = field(default_factory=list)
    # Calibration: stat -> (scale, offset) such that adjusted = scale * raw + offset
    calibration: dict = field(default_factory=dict)

    def predict(
        self, features: dict[str, float], toi_seconds: float = 600,
    ) -> dict[str, float]:
        """Predict per-60 rates from a feature dict.

        For Poisson models, uses toi_seconds as the exposure to convert
        predicted counts back to per-60 rates.

        Returns dict of stat_name -> predicted per-60 rate.
        """
        predictions = {}

        for stat, model in self.models.items():
            vec = self._feature_vector(features, stat)
            if getattr(self, "_use_poisson", False):
                import xgboost as xgb
                if toi_seconds <= 0:
                    pred = 0.0
                else:
                    dmat = xgb.DMatrix(vec)
                    base_margin = np.log(np.array([toi_seconds / 3600]))
                    dmat.set_base_margin(base_margin)
                    pred_count = float(model.predict(dmat)[0])
                    pred = pred_count / toi_seconds * 3600
            else:
                pred = float(model.predict(vec)[0])
                if stat in self.calibration:
                    scale, offset = self.calibration[stat]
                    pred = pred * scale + offset

            predictions[f"{stat}_per60"] = max(0.0, pred)
        return predictions

    def calibrate(
        self,
        calibration_seasons: list[str],
    ) -> dict[str, tuple[float, float]]:
        """Fit post-training calibration on a holdout set.

        Runs predictions on the calibration data and fits a linear correction
        per stat so that predicted mean matches actual mean. This preserves
        the ranking (who's better than who) while fixing systematic bias.

        Uses Platt scaling: for each stat, fits adjusted = scale * raw + offset
        by minimizing MSE between adjusted predictions and actuals.

        Args:
            calibration_seasons: Seasons to use for calibration.

        Returns:
            Dict of stat -> (scale, offset).
        """
        config = SITUATION_CONFIGS.get(self.situation, SITUATION_CONFIGS["5v5"])
        stat_names = config["stats"]
        min_toi = config["min_toi_seconds"]
        min_games = config["min_games"]

        print(f"\n  [{self.situation.upper()}] Calibrating...")
        X_rows, y_rows, discovered_cols = self._extract_training_data(
            calibration_seasons, min_toi, min_games
        )

        if not X_rows:
            print(f"    No calibration data.")
            return {}

        # Re-project calibration features to match training feature columns.
        cal_col_idx = {name: i for i, name in enumerate(discovered_cols)}
        raw_X = np.array(X_rows, dtype=np.float32)

        for stat in stat_names:
            stat_feature_cols = self._get_feature_columns(stat)
            if not stat_feature_cols:
                continue

            X_cal = np.full((len(X_rows), len(stat_feature_cols)), np.nan, dtype=np.float32)
            for i, col in enumerate(stat_feature_cols):
                if col in cal_col_idx:
                    X_cal[:, i] = raw_X[:, cal_col_idx[col]]

            target_col = STAT_TARGETS.get(stat, stat)
            y_vals = [y.get(target_col) for y in y_rows]

            valid = [(i, y) for i, y in enumerate(y_vals)
                     if y is not None and np.isfinite(y)]
            if len(valid) < 100:
                continue

            indices, targets = zip(*valid)
            X = X_cal[list(indices)]
            y_actual = np.array(targets, dtype=np.float32)

            y_pred = self.models[stat].predict(X)

            # Fit linear calibration: y_actual ≈ scale * y_pred + offset
            # Using least squares: [y_pred, 1] @ [scale, offset] = y_actual
            A = np.column_stack([y_pred, np.ones(len(y_pred))])
            result = np.linalg.lstsq(A, y_actual, rcond=None)
            scale, offset = result[0]

            self.calibration[stat] = (float(scale), float(offset))

            # Report the correction
            raw_mean = y_pred.mean()
            adj_mean = raw_mean * scale + offset
            actual_mean = y_actual.mean()
            print(f"    {stat}: scale={scale:.3f} offset={offset:+.3f} "
                  f"(raw_mean={raw_mean:.3f} → adj={adj_mean:.3f}, actual={actual_mean:.3f})")

        return self.calibration

    def train(
        self,
        train_seasons: list[str],
        use_poisson: bool = False,
    ) -> dict[str, int]:
        """Train on specified seasons using walk-forward protocol.

        Args:
            train_seasons: Seasons to train on.
            use_poisson: If True, use Poisson count regression with TOI offset
                instead of standard regression on per-60 rates. Better for
                rare events (PK shots/hits/blocks) because the target is clean
                integer counts instead of noisy per-60 rates.

        Returns dict of stat -> number of training samples.
        """
        from xgboost import XGBRegressor

        config = SITUATION_CONFIGS.get(self.situation, SITUATION_CONFIGS["5v5"])
        stat_names = config["stats"]
        min_toi = config["min_toi_seconds"]
        min_games = config["min_games"]

        self._use_poisson = use_poisson

        print(f"\n  [{self.situation.upper()}] Extracting training data "
              f"({'Poisson' if use_poisson else 'regression'})...")
        X_rows, y_rows, feature_cols = self._extract_training_data(
            train_seasons, min_toi, min_games
        )

        if not X_rows:
            print(f"  [{self.situation.upper()}] No training data.")
            return {}

        all_feature_cols = feature_cols
        X_all = np.array(X_rows, dtype=np.float32)

        # Extract TOI for each sample (used for weighting and Poisson offset)
        toi_all = np.array([y.get("_toi_seconds", 600) for y in y_rows],
                           dtype=np.float32)

        # Per-stat feature filtering
        self.feature_columns = {}

        sample_counts = {}
        for stat in stat_names:
            # Filter features for this stat
            stat_col_mask = [
                feature_allowed_for_stat(col, stat)
                for col in all_feature_cols
            ]
            stat_cols = [c for c, m in zip(all_feature_cols, stat_col_mask) if m]
            stat_col_indices = [i for i, m in enumerate(stat_col_mask) if m]
            self.feature_columns[stat] = stat_cols

            X_stat = X_all[:, stat_col_indices]

            target_col = STAT_TARGETS.get(stat, stat)

            if use_poisson:
                y_vals = [y.get(f"_raw_{target_col}") for y in y_rows]
            else:
                y_vals = [y.get(target_col) for y in y_rows]

            valid = [(i, y) for i, y in enumerate(y_vals)
                     if y is not None and np.isfinite(y)]
            if not valid:
                print(f"    {stat}: no valid targets, skipping")
                continue

            indices, targets = zip(*valid)
            X = X_stat[list(indices)]
            y = np.array(targets, dtype=np.float32)
            toi = toi_all[list(indices)]

            if use_poisson:
                # Poisson regression: predict counts with TOI as exposure
                # base_margin = log(TOI/3600) so predictions are in per-hour rates
                import xgboost as xgb
                base_margin = np.log(toi / 3600)

                dtrain = xgb.DMatrix(X, label=y)
                dtrain.set_base_margin(base_margin)

                params = {
                    "objective": "count:poisson",
                    "max_depth": 4,
                    "min_child_weight": 20,
                    "learning_rate": 0.03,
                    "subsample": 0.8,
                    "colsample_bytree": 0.7,
                    "seed": 42,
                }
                bst = xgb.train(params, dtrain, num_boost_round=300)
                self.models[stat] = bst
                sample_counts[stat] = len(y)
                mean_count = y.mean()
                mean_toi = toi.mean()
                implied_rate = mean_count / mean_toi * 3600
                print(f"    {stat}: trained on {len(y)} samples (Poisson), "
                      f"mean count={mean_count:.3f}, mean TOI={mean_toi:.0f}s, "
                      f"implied rate/60={implied_rate:.3f}")
            else:
                # Standard regression on per-60 rates, weighted by sqrt(TOI).
                # PP needs weighting too: training set includes 4th-line cameos
                # but evaluation/usage is on top-PP players, so unweighted training
                # underpredicts. sqrt(TOI) shifts training toward the right pool.
                sample_weights = np.sqrt(toi)

                model = XGBRegressor(
                    n_estimators=300,
                    max_depth=5,
                    learning_rate=0.05,
                    subsample=0.8,
                    colsample_bytree=0.7,
                    min_child_weight=10,
                    random_state=42,
                )
                model.fit(X, y, sample_weight=sample_weights)
                self.models[stat] = model
                sample_counts[stat] = len(y)
                print(f"    {stat}: trained on {len(y)} samples, "
                      f"{len(stat_cols)} features, "
                      f"mean target={y.mean():.3f} (TOI-weighted)")

        return sample_counts

    def _extract_training_data(
        self,
        seasons: list[str],
        min_toi: int,
        min_games: int,
    ) -> tuple[list, list, list]:
        """Walk-forward extraction of features and targets.

        Returns (X_rows, y_rows, feature_columns) where:
            X_rows: list of feature vectors (numpy arrays)
            y_rows: list of target dicts (stat -> per-60 rate)
            feature_columns: ordered list of feature names
        """
        from collections import defaultdict

        X_rows = []
        y_rows = []
        feature_columns = None
        games_played = defaultdict(int)  # player_id -> count

        # Determine query situation
        config = SITUATION_CONFIGS.get(self.situation, {})
        source_situations = config.get("source_situations")

        with get_session() as session:
            for season in seasons:
                start_year = int(season[:4])
                start_gid = start_year * 1_000_000
                end_gid = (start_year + 1) * 1_000_000

                # Determine the query situation for game_advanced_stats
                if source_situations:
                    sit_filter = f"gas.situation IN ({','.join(repr(s) for s in source_situations)})"
                else:
                    sit_filter = f"gas.situation = '{self.situation}'"

                # Get all game dates with data in this situation
                dates = session.execute(
                    text(f"""
                        SELECT DISTINCT g.date
                        FROM game_advanced_stats gas
                        JOIN games g ON gas.game_id = g.game_id
                        WHERE {sit_filter}
                              AND gas.game_id >= :start AND gas.game_id < :end
                              AND gas.toi_seconds >= :min_toi
                        ORDER BY g.date
                    """),
                    {"start": start_gid, "end": end_gid, "min_toi": min_toi},
                ).fetchall()
                game_dates = [d[0] for d in dates]

                total_dates = len(game_dates)
                print(f"    Season {season}: {total_dates} game dates")

                for date_idx, game_date in enumerate(game_dates):
                    if (date_idx + 1) % 50 == 0:
                        print(f"      Date {date_idx + 1}/{total_dates}...", flush=True)

                    # Get all player-game rows for this date
                    player_rows = session.execute(
                        text(f"""
                            SELECT gas.player_id, gas.team_id, gas.opponent_team_id,
                                   gas.toi_seconds, gas.goals, gas.assists,
                                   gas.shots, gas.hits, gas.blocks, gas.game_id,
                                   g.home_team_id
                            FROM game_advanced_stats gas
                            JOIN games g ON gas.game_id = g.game_id
                            WHERE {sit_filter}
                                  AND g.date = :gd
                                  AND gas.game_id >= :start AND gas.game_id < :end
                                  AND gas.toi_seconds >= :min_toi
                        """),
                        {"gd": game_date, "start": start_gid,
                         "end": end_gid, "min_toi": min_toi},
                    ).fetchall()

                    for pr in player_rows:
                        player_id = pr[0]
                        team_id = pr[1]
                        opp_team_id = pr[2]
                        toi_seconds = pr[3]
                        game_id = pr[9]
                        home_team_id = pr[10]

                        # Skip until player has enough prior games
                        if games_played[player_id] < min_games:
                            games_played[player_id] += 1
                            continue

                        # Get player position
                        player = session.query(Player).filter(
                            Player.nhl_id == player_id
                        ).first()
                        position = player.position if player else "C"

                        # Extract features
                        features = extract_all_features(
                            session,
                            player_id=player_id,
                            situation=self.situation,
                            game_date=game_date,
                            team_id=team_id,
                            opponent_team_id=opp_team_id,
                            home_team_id=home_team_id,
                            position=position,
                            current_season_start_year=start_year,
                        )

                        # Determine feature columns from first extraction
                        if feature_columns is None:
                            feature_columns = sorted(features.keys())

                        # Build feature vector
                        vec = np.array(
                            [features.get(col, np.nan) for col in feature_columns],
                            dtype=np.float32,
                        )
                        vec[~np.isfinite(vec)] = np.nan

                        # Build target dict
                        targets = {"_toi_seconds": toi_seconds}
                        stat_col_indices = {
                            "goals": 4, "assists": 5, "shots": 6,
                            "hits": 7, "blocks": 8,
                        }
                        for stat, col in STAT_TARGETS.items():
                            idx = stat_col_indices.get(col)
                            if idx is not None:
                                raw = pr[idx] or 0
                                # Store both per-60 rate and raw count
                                targets[col] = safe_per_60(raw, toi_seconds)
                                targets[f"_raw_{col}"] = float(raw)

                        X_rows.append(vec)
                        y_rows.append(targets)
                        games_played[player_id] += 1

        print(f"    Extracted {len(X_rows)} training samples")
        return X_rows, y_rows, feature_columns or []

    def _get_feature_columns(self, stat: str) -> list[str]:
        """Get feature columns for a stat, handling both old and new formats."""
        if isinstance(self.feature_columns, dict):
            return self.feature_columns.get(stat, [])
        return self.feature_columns

    def _feature_vector(self, features: dict[str, float], stat: str = None) -> np.ndarray:
        """Convert a feature dict to a numpy array matching training columns."""
        cols = self._get_feature_columns(stat) if stat else self.feature_columns
        vec = np.array(
            [features.get(col, np.nan) for col in cols],
            dtype=np.float32,
        ).reshape(1, -1)
        vec[~np.isfinite(vec)] = np.nan
        return vec

    def save(self, path: str | Path | None = None) -> Path:
        if path is None:
            path = MODEL_DIR / f"{self.situation}_model.pkl"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "situation": self.situation,
                "models": self.models,
                "feature_columns": self.feature_columns,
                "calibration": self.calibration,
                "use_poisson": getattr(self, "_use_poisson", False),
            }, f)
        print(f"  Saved {self.situation} model to {path}")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "SituationModel":
        with open(path, "rb") as f:
            data = pickle.load(f)
        model = cls(situation=data["situation"])
        model.models = data["models"]
        model.feature_columns = data["feature_columns"]
        model.calibration = data.get("calibration", {})
        model._use_poisson = data.get("use_poisson", False)
        return model

    def feature_importance(self, stat: str, top_n: int = 15) -> list[tuple[str, float]]:
        if stat not in self.models:
            return []
        importances = self.models[stat].feature_importances_
        cols = self._get_feature_columns(stat)
        pairs = list(zip(cols, importances))
        pairs.sort(key=lambda x: x[1], reverse=True)
        return pairs[:top_n]

"""XGBoost forecast model.

Trains a gradient-boosted tree per stat using extracted features. Unlike the
baseline models which use fixed formulas, this learns feature weights and
nonlinear interactions from data.

Training workflow:
    1. Extract features for every player-game in a training season
    2. Fit one XGBRegressor per forecast stat
    3. Save the trained model to disk (pickle)

Prediction:
    predict(feature_set) reads the same feature keys the extractors produce,
    builds a feature vector, and runs inference.
"""

import pickle
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np

from src.core.db import get_session
from src.core.models import GameIndividualStats, Player
from src.tools.forecasting.evaluation import STAT_COLUMN_MAP
from src.tools.forecasting.models import FeatureSet

# Features the model expects, in order. Must match what the extractors produce.
# Grouped by extractor for clarity.
FEATURE_COLUMNS = [
    # RollingIndividualExtractor — season averages
    "season_gp",
    "season_avg_goals_per_60",
    "season_avg_assists_per_60",
    "season_avg_shots_per_60",
    "season_avg_ixg_per_60",
    "season_avg_icf_per_60",
    "season_avg_iscf_per_60",
    "season_avg_hits_per_60",
    "season_avg_blocked_per_60",
    "season_avg_toi",
    "season_avg_sh_pct",
    "season_avg_ipp",
    # RollingIndividualExtractor — rolling 5-game
    "rolling_5_goals_per_60",
    "rolling_5_assists_per_60",
    "rolling_5_shots_per_60",
    "rolling_5_ixg_per_60",
    "rolling_5_icf_per_60",
    "rolling_5_iscf_per_60",
    "rolling_5_hits_per_60",
    "rolling_5_blocked_per_60",
    "rolling_5_toi",
    "rolling_5_sh_pct",
    "rolling_5_ipp",
    # SeasonAggregateExtractor — prior season
    "prior_season_gp",
    "prior_season_goals_per_gp",
    "prior_season_assists_per_gp",
    "prior_season_shots_per_gp",
    "prior_season_hits_per_gp",
    "prior_season_blocked_per_gp",
    "prior_season_pim_per_gp",
    "prior_season_sh_pct",
    "prior_season_ipp",
    # GameContextExtractor
    "is_home",
    "is_b2b",
    # OpponentExtractor
    "opp_gaa",
    "opp_gfa",
    "opp_rolling_5_gaa",
    "opp_is_b2b",
]


def _feature_vector(fs: FeatureSet) -> np.ndarray:
    """Convert a FeatureSet to a fixed-length numpy array.

    Missing features become NaN — XGBoost handles these natively.
    """
    vec = np.array(
        [fs.features.get(col, np.nan) for col in FEATURE_COLUMNS],
        dtype=np.float32,
    )
    # Replace inf with NaN — NST per-60 rates can blow up with tiny TOI
    vec[~np.isfinite(vec)] = np.nan
    return vec


@dataclass
class XGBoostModel:
    """XGBoost forecast model — one regressor per stat.

    Must be trained before use. Call train() with a season of data,
    or load a previously trained model from disk with load().
    """

    name: str = "xgboost"
    models: dict = field(default_factory=dict)  # stat -> fitted XGBRegressor
    _feature_columns: list = field(default_factory=lambda: list(FEATURE_COLUMNS))

    def predict(self, features: FeatureSet) -> dict[str, float]:
        vec = _feature_vector(features).reshape(1, -1)
        predictions = {}
        for stat, model in self.models.items():
            pred = model.predict(vec)[0]
            predictions[stat] = max(0.0, float(pred))
        return predictions

    def train(
        self,
        season: str,
        feature_extractors: list,
        situation: str = "all",
        min_games: int = 10,
    ) -> dict[str, int]:
        """Train on a full season of data.

        Extracts features for every player-game, then fits one XGBRegressor
        per forecast stat.

        Returns dict of stat -> number of training samples.
        """
        from xgboost import XGBRegressor

        print(f"Extracting training features for season {season}...")
        X_rows, y_rows = self._extract_training_data(
            season, feature_extractors, situation, min_games
        )

        sample_counts = {}
        for stat, actual_col in STAT_COLUMN_MAP.items():
            # Build X/y arrays, dropping rows where target is missing
            X = []
            y = []
            for features, actuals in zip(X_rows, y_rows):
                if actual_col in actuals and actuals[actual_col] is not None:
                    X.append(features)
                    y.append(actuals[actual_col])

            if not X:
                print(f"  {stat}: no training data, skipping")
                continue

            X_arr = np.array(X, dtype=np.float32)
            y_arr = np.array(y, dtype=np.float32)

            model = XGBRegressor(
                n_estimators=200,
                max_depth=5,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_weight=10,
                random_state=42,
            )
            model.fit(X_arr, y_arr)
            self.models[stat] = model
            sample_counts[stat] = len(y)
            print(f"  {stat}: trained on {len(y)} samples")

        return sample_counts

    def _extract_training_data(
        self,
        season: str,
        feature_extractors: list,
        situation: str,
        min_games: int,
    ) -> tuple[list[np.ndarray], list[dict]]:
        """Walk through a season and extract feature vectors + actual values."""
        from sqlalchemy import distinct

        X_rows = []
        y_rows = []
        games_played_count: defaultdict[int, int] = defaultdict(int)

        with get_session() as session:
            game_dates = (
                session.query(distinct(GameIndividualStats.game_date))
                .filter(
                    GameIndividualStats.season == season,
                    GameIndividualStats.situation == situation,
                )
                .order_by(GameIndividualStats.game_date)
                .all()
            )
            game_dates = [d[0] for d in game_dates]

            total_dates = len(game_dates)
            for i, game_date in enumerate(game_dates):
                if (i + 1) % 50 == 0:
                    print(f"  Processing date {i + 1}/{total_dates}...")

                game_stats = (
                    session.query(GameIndividualStats)
                    .filter(
                        GameIndividualStats.game_date == game_date,
                        GameIndividualStats.season == season,
                        GameIndividualStats.situation == situation,
                    )
                    .all()
                )

                for gs in game_stats:
                    nhl_id = gs.nhl_id

                    if games_played_count[nhl_id] < min_games:
                        games_played_count[nhl_id] += 1
                        continue

                    fs = FeatureSet(
                        nhl_id=nhl_id,
                        game_id=gs.game_id,
                        game_date=game_date,
                    )

                    for extractor in feature_extractors:
                        extractor.extract(session, fs, before_date=game_date)

                    vec = _feature_vector(fs)

                    # Collect actual values for all forecast stats
                    actuals = {}
                    for stat, col in STAT_COLUMN_MAP.items():
                        val = getattr(gs, col, None)
                        if val is not None:
                            actuals[col] = val

                    X_rows.append(vec)
                    y_rows.append(actuals)
                    games_played_count[nhl_id] += 1

        print(f"  Extracted {len(X_rows)} training samples from {total_dates} game dates")
        return X_rows, y_rows

    def save(self, path: str | Path) -> None:
        """Save trained model to disk."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(
                {"models": self.models, "feature_columns": self._feature_columns},
                f,
            )
        print(f"Saved model to {path}")

    @classmethod
    def load(cls, path: str | Path) -> "XGBoostModel":
        """Load a trained model from disk."""
        with open(path, "rb") as f:
            data = pickle.load(f)
        model = cls()
        model.models = data["models"]
        model._feature_columns = data["feature_columns"]
        return model

    def feature_importance(self, stat: str, top_n: int = 15) -> list[tuple[str, float]]:
        """Get top feature importances for a stat."""
        if stat not in self.models:
            return []
        importances = self.models[stat].feature_importances_
        pairs = list(zip(FEATURE_COLUMNS, importances))
        pairs.sort(key=lambda x: x[1], reverse=True)
        return pairs[:top_n]

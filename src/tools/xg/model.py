"""XGBoost expected goals (xG) model.

Trains gradient-boosted classifiers to predict the probability that a shot
attempt results in a goal. Follows the Evolving Hockey approach of training
separate models per strength state (5v5, PP, PK, empty net).

Training workflow:
    1. Read feature-enriched shot attempts from the shot_attempts table
    2. Build feature matrix + binary labels (is_goal)
    3. Train one XGBClassifier per strength state
    4. Evaluate with temporal holdout (train on older seasons, test on newer)
    5. Save trained models to disk

Prediction:
    predict(features_dict) returns xG probability for a single shot.
    predict_batch(df) returns xG probabilities for a DataFrame of shots.
"""

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from sqlalchemy import text
from src.core.db import get_session

# Features used by the model, in order. Must match columns in shot_attempts.
FEATURE_COLUMNS = [
    # Geometric
    "distance_to_net",
    "angle_to_net",
    # Shot info
    "shot_type_wrist",
    "shot_type_slap",
    "shot_type_snap",
    "shot_type_tip_in",
    "shot_type_backhand",
    "shot_type_deflected",
    "shot_type_wrap_around",
    "shot_type_poke",
    "shot_type_bat",
    "shot_type_cradle",
    "shot_type_between_legs",
    # Game state
    "score_differential",
    "is_home",
    "period",
    # Sequence
    "time_since_last_event",
    "distance_from_last_event",
    "angle_change_from_last_shot",
    "is_rebound",
    "is_rush",
    "flurry_count",
    # Last event type (one-hot)
    "last_event_shot_on_goal",
    "last_event_missed_shot",
    "last_event_blocked_shot",
    "last_event_goal",
    "last_event_faceoff",
    "last_event_hit",
    "last_event_giveaway",
    "last_event_takeaway",
]

# Strength states that get their own model
STRENGTH_GROUPS = {
    "5v5": ["5v5"],
    "pp": ["5v4", "5v3", "4v3"],
    "pk": ["4v5", "3v5", "3v4"],
    "en": [],  # Empty net — determined by goalie_id being null
    "other": [],  # Catch-all
}

# Shot types for one-hot encoding
SHOT_TYPES = [
    "wrist", "slap", "snap", "tip-in", "backhand", "deflected",
    "wrap-around", "poke", "bat", "cradle", "between-legs",
]

# Last event types for one-hot encoding
LAST_EVENT_TYPES = [
    "shot-on-goal", "missed-shot", "blocked-shot", "goal",
    "faceoff", "hit", "giveaway", "takeaway",
]

MODEL_DIR = Path("models/xg")


def _classify_strength(strength_state: str, goalie_id) -> str:
    """Map a strength state + goalie presence to a model group."""
    if goalie_id is None:
        return "en"
    if strength_state in ("5v5",):
        return "5v5"
    if strength_state in ("5v4", "5v3", "4v3"):
        return "pp"
    if strength_state in ("4v5", "3v5", "3v4"):
        return "pk"
    return "other"


def load_shot_data(
    seasons: list[str] | None = None,
    min_game_id: int | None = None,
    max_game_id: int | None = None,
) -> pd.DataFrame:
    """Load shot attempts from DB into a DataFrame.

    Args:
        seasons: Filter by season (derived from game_id ranges).
        min_game_id: Minimum game ID to include.
        max_game_id: Maximum game ID (exclusive).

    Returns:
        DataFrame with all shot_attempts columns.
    """
    conditions = []
    params = {}

    if min_game_id is not None:
        conditions.append("game_id >= :min_gid")
        params["min_gid"] = min_game_id
    if max_game_id is not None:
        conditions.append("game_id < :max_gid")
        params["max_gid"] = max_game_id
    if seasons:
        # Game IDs encode season: 2024020001 = 2024-25 season
        season_conditions = []
        for i, season in enumerate(seasons):
            start_year = int(season[:4])
            key_lo = f"lo_{i}"
            key_hi = f"hi_{i}"
            season_conditions.append(f"(game_id >= :{key_lo} AND game_id < :{key_hi})")
            params[key_lo] = start_year * 1_000_000
            params[key_hi] = (start_year + 1) * 1_000_000
        conditions.append(f"({' OR '.join(season_conditions)})")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    query = f"""
        SELECT game_id, event_id, shooter_id, goalie_id, team_id,
               period, game_seconds, situation_code, strength_state,
               score_differential, is_home,
               distance_to_net, angle_to_net,
               event_type, shot_type, is_goal,
               time_since_last_event, distance_from_last_event,
               last_event_type, angle_change_from_last_shot,
               is_rebound, is_rush, flurry_count
        FROM shot_attempts
        {where}
        ORDER BY game_id, game_seconds
    """

    with get_session() as session:
        result = session.execute(text(query), params)
        columns = result.keys()
        rows = result.fetchall()

    df = pd.DataFrame(rows, columns=columns)
    return df


def build_feature_matrix(df: pd.DataFrame) -> np.ndarray:
    """Convert a DataFrame of shot attempts into a feature matrix.

    Handles one-hot encoding of shot_type and last_event_type.
    Returns a 2D numpy array with columns matching FEATURE_COLUMNS.
    """
    n = len(df)
    n_features = len(FEATURE_COLUMNS)
    X = np.full((n, n_features), np.nan, dtype=np.float32)

    col_idx = {name: i for i, name in enumerate(FEATURE_COLUMNS)}

    # Numeric features — direct copy
    for col in ["distance_to_net", "angle_to_net", "score_differential",
                "period", "time_since_last_event", "distance_from_last_event",
                "angle_change_from_last_shot", "flurry_count"]:
        if col in df.columns and col in col_idx:
            X[:, col_idx[col]] = df[col].values.astype(np.float32)

    # Boolean features
    for col in ["is_home", "is_rebound", "is_rush"]:
        if col in df.columns and col in col_idx:
            X[:, col_idx[col]] = df[col].astype(float).values

    # One-hot: shot_type
    for shot_type in SHOT_TYPES:
        col_name = f"shot_type_{shot_type.replace('-', '_')}"
        if col_name in col_idx:
            X[:, col_idx[col_name]] = (df["shot_type"] == shot_type).astype(np.float32).values

    # One-hot: last_event_type
    for event_type in LAST_EVENT_TYPES:
        col_name = f"last_event_{event_type.replace('-', '_')}"
        if col_name in col_idx:
            X[:, col_idx[col_name]] = (df["last_event_type"] == event_type).astype(np.float32).values

    return X


@dataclass
class XGModel:
    """Expected goals model — one classifier per strength state.

    Train with train(), predict with predict() or predict_batch().
    """

    models: dict = field(default_factory=dict)  # strength_group -> fitted XGBClassifier
    _feature_columns: list = field(default_factory=lambda: list(FEATURE_COLUMNS))

    def predict(self, features: dict) -> float:
        """Predict xG for a single shot.

        Args:
            features: Dict with keys matching FEATURE_COLUMNS.

        Returns:
            Probability of goal (0-1).
        """
        vec = np.array(
            [features.get(col, np.nan) for col in self._feature_columns],
            dtype=np.float32,
        ).reshape(1, -1)

        strength_group = features.get("_strength_group", "5v5")
        model = self.models.get(strength_group)
        if model is None:
            # Fall back to 5v5 model
            model = self.models.get("5v5")
        if model is None:
            return 0.0

        return float(model.predict_proba(vec)[:, 1][0])

    def predict_batch(self, df: pd.DataFrame) -> np.ndarray:
        """Predict xG for a DataFrame of shots.

        Returns array of probabilities, one per row.
        """
        X = build_feature_matrix(df)

        # Classify each shot into a strength group
        groups = df.apply(
            lambda row: _classify_strength(row["strength_state"], row["goalie_id"]),
            axis=1,
        ).values

        xg = np.zeros(len(df), dtype=np.float64)

        for group_name, model in self.models.items():
            mask = groups == group_name
            if mask.sum() == 0:
                continue
            xg[mask] = model.predict_proba(X[mask])[:, 1]

        # For shots with no matching model, use 5v5 as fallback
        unmatched = ~np.isin(groups, list(self.models.keys()))
        if unmatched.sum() > 0 and "5v5" in self.models:
            xg[unmatched] = self.models["5v5"].predict_proba(X[unmatched])[:, 1]

        return xg

    def train(
        self,
        train_seasons: list[str],
        min_shots_for_group: int = 500,
    ) -> dict[str, dict]:
        """Train xG models on specified seasons.

        Args:
            train_seasons: Seasons to use for training (e.g., ["20192020", ..., "20242025"]).
            min_shots_for_group: Minimum shots needed to train a separate model
                for a strength group. Groups below this threshold are merged
                into "other" and use the 5v5 model.

        Returns:
            Dict of strength_group -> {"n_shots", "n_goals", "goal_rate"}.
        """
        from xgboost import XGBClassifier

        print(f"Loading shot data for seasons: {train_seasons}")
        df = load_shot_data(seasons=train_seasons)
        print(f"  Loaded {len(df)} shot attempts, {df['is_goal'].sum()} goals "
              f"({df['is_goal'].mean()*100:.1f}%)")

        if len(df) == 0:
            print("  No data — aborting training.")
            return {}

        # Classify shots into strength groups
        df["_strength_group"] = df.apply(
            lambda row: _classify_strength(row["strength_state"], row["goalie_id"]),
            axis=1,
        )

        # Build full feature matrix once
        X_all = build_feature_matrix(df)
        y_all = df["is_goal"].astype(int).values

        results = {}

        for group_name in ["5v5", "pp", "pk", "en", "other"]:
            mask = df["_strength_group"] == group_name
            n_shots = mask.sum()

            if n_shots < min_shots_for_group:
                print(f"  {group_name}: {n_shots} shots (below threshold, will use 5v5 fallback)")
                continue

            X = X_all[mask]
            y = y_all[mask]
            n_goals = y.sum()
            goal_rate = y.mean()

            print(f"  Training {group_name}: {n_shots} shots, {n_goals} goals "
                  f"({goal_rate*100:.1f}%)")

            model = XGBClassifier(
                n_estimators=300,
                max_depth=6,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_weight=10,
                eval_metric="logloss",
                random_state=42,
            )
            model.fit(X, y)
            self.models[group_name] = model

            results[group_name] = {
                "n_shots": int(n_shots),
                "n_goals": int(n_goals),
                "goal_rate": float(goal_rate),
            }

        return results

    def evaluate(
        self,
        test_seasons: list[str],
    ) -> dict:
        """Evaluate model on holdout seasons.

        Returns dict with AUC, log loss, Brier score, calibration data.
        """
        from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss

        print(f"Loading test data for seasons: {test_seasons}")
        df = load_shot_data(seasons=test_seasons)
        print(f"  Loaded {len(df)} shot attempts, {df['is_goal'].sum()} goals")

        if len(df) == 0:
            return {}

        xg_pred = self.predict_batch(df)
        y_true = df["is_goal"].astype(int).values

        # Overall metrics
        auc = roc_auc_score(y_true, xg_pred)
        ll = log_loss(y_true, xg_pred)
        brier = brier_score_loss(y_true, xg_pred)

        print(f"\n  Overall results:")
        print(f"    AUC:         {auc:.4f}")
        print(f"    Log loss:    {ll:.4f}")
        print(f"    Brier score: {brier:.4f}")

        # Per strength group
        df["_strength_group"] = df.apply(
            lambda row: _classify_strength(row["strength_state"], row["goalie_id"]),
            axis=1,
        )

        group_results = {}
        for group_name in df["_strength_group"].unique():
            mask = df["_strength_group"] == group_name
            if mask.sum() < 50:
                continue
            g_auc = roc_auc_score(y_true[mask], xg_pred[mask])
            g_ll = log_loss(y_true[mask], xg_pred[mask])
            n = mask.sum()
            n_goals = y_true[mask].sum()
            print(f"    {group_name:6s}: AUC={g_auc:.4f} LL={g_ll:.4f} "
                  f"({n} shots, {n_goals} goals)")
            group_results[group_name] = {
                "auc": float(g_auc),
                "log_loss": float(g_ll),
                "n_shots": int(n),
                "n_goals": int(n_goals),
            }

        # Calibration: predicted vs actual by decile
        calibration = _compute_calibration(y_true, xg_pred)
        print(f"\n  Calibration (predicted vs actual by decile):")
        for bucket in calibration:
            print(f"    {bucket['bin_label']:12s}: predicted={bucket['predicted']:.4f} "
                  f"actual={bucket['actual']:.4f} n={bucket['count']}")

        return {
            "auc": float(auc),
            "log_loss": float(ll),
            "brier_score": float(brier),
            "n_shots": len(df),
            "n_goals": int(y_true.sum()),
            "groups": group_results,
            "calibration": calibration,
        }

    def save(self, path: str | Path | None = None) -> Path:
        """Save trained models to disk."""
        if path is None:
            path = MODEL_DIR / "xg_latest.pkl"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(
                {"models": self.models, "feature_columns": self._feature_columns},
                f,
            )
        print(f"Saved xG model to {path}")
        return path

    @classmethod
    def load(cls, path: str | Path | None = None) -> "XGModel":
        """Load trained models from disk."""
        if path is None:
            path = MODEL_DIR / "xg_latest.pkl"
        with open(path, "rb") as f:
            data = pickle.load(f)
        model = cls()
        model.models = data["models"]
        model._feature_columns = data["feature_columns"]
        return model

    def feature_importance(self, group: str = "5v5", top_n: int = 15) -> list[tuple[str, float]]:
        """Get top feature importances for a strength group."""
        if group not in self.models:
            return []
        importances = self.models[group].feature_importances_
        pairs = list(zip(self._feature_columns, importances))
        pairs.sort(key=lambda x: x[1], reverse=True)
        return pairs[:top_n]


def _compute_calibration(y_true: np.ndarray, y_pred: np.ndarray, n_bins: int = 10) -> list[dict]:
    """Compute calibration data: predicted vs actual by quantile bucket."""
    # Use quantile-based bins for even sample sizes
    try:
        bin_edges = np.quantile(y_pred, np.linspace(0, 1, n_bins + 1))
        # Ensure unique edges
        bin_edges = np.unique(bin_edges)
    except Exception:
        return []

    buckets = []
    for i in range(len(bin_edges) - 1):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        if i == len(bin_edges) - 2:
            mask = (y_pred >= lo) & (y_pred <= hi)
        else:
            mask = (y_pred >= lo) & (y_pred < hi)

        if mask.sum() == 0:
            continue

        buckets.append({
            "bin_label": f"{lo:.3f}-{hi:.3f}",
            "predicted": float(y_pred[mask].mean()),
            "actual": float(y_true[mask].mean()),
            "count": int(mask.sum()),
        })

    return buckets

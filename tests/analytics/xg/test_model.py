"""Tests for xG model feature matrix construction."""

import numpy as np
import pandas as pd
import pytest

from src.analytics.xg.model import (
    FEATURE_COLUMNS,
    SHOT_TYPES,
    LAST_EVENT_TYPES,
    build_feature_matrix,
    _classify_strength,
)


def _make_shot_row(**overrides) -> dict:
    """Create a synthetic shot attempt row with defaults."""
    row = {
        "game_id": 2025020001,
        "event_id": 1,
        "shooter_id": 8478402,
        "goalie_id": 8476883,
        "team_id": 22,
        "period": 1,
        "game_seconds": 300,
        "situation_code": "1551",
        "strength_state": "5v5",
        "score_differential": 0,
        "is_home": True,
        "distance_to_net": 30.0,
        "angle_to_net": 15.0,
        "event_type": "shot-on-goal",
        "shot_type": "wrist",
        "is_goal": False,
        "time_since_last_event": 5.0,
        "distance_from_last_event": 20.0,
        "last_event_type": "hit",
        "angle_change_from_last_shot": 10.0,
        "is_rebound": False,
        "is_rush": False,
        "flurry_count": 1,
    }
    row.update(overrides)
    return row


class TestBuildFeatureMatrix:
    def test_output_shape(self):
        df = pd.DataFrame([_make_shot_row(), _make_shot_row()])
        X = build_feature_matrix(df)
        assert X.shape == (2, len(FEATURE_COLUMNS))

    def test_single_shot_shape(self):
        df = pd.DataFrame([_make_shot_row()])
        X = build_feature_matrix(df)
        assert X.shape == (1, len(FEATURE_COLUMNS))

    def test_numeric_features_copied(self):
        df = pd.DataFrame([_make_shot_row(distance_to_net=42.5, angle_to_net=18.3)])
        X = build_feature_matrix(df)
        col_idx = {name: i for i, name in enumerate(FEATURE_COLUMNS)}
        assert abs(X[0, col_idx["distance_to_net"]] - 42.5) < 1e-4
        assert abs(X[0, col_idx["angle_to_net"]] - 18.3) < 1e-4

    def test_shot_type_one_hot_exactly_one(self):
        col_idx = {name: i for i, name in enumerate(FEATURE_COLUMNS)}
        shot_type_cols = [i for name, i in col_idx.items() if name.startswith("shot_type_")]

        for st in SHOT_TYPES:
            df = pd.DataFrame([_make_shot_row(shot_type=st)])
            X = build_feature_matrix(df)
            one_hot = X[0, shot_type_cols]
            assert one_hot.sum() == 1.0, f"shot_type={st} should have exactly one 1.0"

    def test_shot_type_unknown_all_zeros(self):
        col_idx = {name: i for i, name in enumerate(FEATURE_COLUMNS)}
        shot_type_cols = [i for name, i in col_idx.items() if name.startswith("shot_type_")]

        df = pd.DataFrame([_make_shot_row(shot_type="unknown_type")])
        X = build_feature_matrix(df)
        one_hot = X[0, shot_type_cols]
        assert one_hot.sum() == 0.0

    def test_last_event_type_one_hot_exactly_one(self):
        col_idx = {name: i for i, name in enumerate(FEATURE_COLUMNS)}
        event_cols = [i for name, i in col_idx.items() if name.startswith("last_event_")]

        for et in LAST_EVENT_TYPES:
            df = pd.DataFrame([_make_shot_row(last_event_type=et)])
            X = build_feature_matrix(df)
            one_hot = X[0, event_cols]
            assert one_hot.sum() == 1.0, f"last_event_type={et} should have exactly one 1.0"

    def test_last_event_type_unknown_all_zeros(self):
        col_idx = {name: i for i, name in enumerate(FEATURE_COLUMNS)}
        event_cols = [i for name, i in col_idx.items() if name.startswith("last_event_")]

        df = pd.DataFrame([_make_shot_row(last_event_type="penalty")])
        X = build_feature_matrix(df)
        one_hot = X[0, event_cols]
        assert one_hot.sum() == 0.0

    def test_boolean_features_are_0_or_1(self):
        col_idx = {name: i for i, name in enumerate(FEATURE_COLUMNS)}
        df = pd.DataFrame([
            _make_shot_row(is_home=True, is_rebound=True, is_rush=False),
            _make_shot_row(is_home=False, is_rebound=False, is_rush=True),
        ])
        X = build_feature_matrix(df)
        assert X[0, col_idx["is_home"]] == 1.0
        assert X[0, col_idx["is_rebound"]] == 1.0
        assert X[0, col_idx["is_rush"]] == 0.0
        assert X[1, col_idx["is_home"]] == 0.0
        assert X[1, col_idx["is_rush"]] == 1.0

    def test_nan_for_missing_time_since_last(self):
        df = pd.DataFrame([_make_shot_row(time_since_last_event=None)])
        X = build_feature_matrix(df)
        col_idx = {name: i for i, name in enumerate(FEATURE_COLUMNS)}
        assert np.isnan(X[0, col_idx["time_since_last_event"]])

    def test_feature_count_is_30(self):
        assert len(FEATURE_COLUMNS) == 30


class TestClassifyStrength:
    def test_5v5(self):
        assert _classify_strength("5v5", 123) == "5v5"

    def test_pp(self):
        assert _classify_strength("5v4", 123) == "pp"
        assert _classify_strength("5v3", 123) == "pp"
        assert _classify_strength("4v3", 123) == "pp"

    def test_pk(self):
        assert _classify_strength("4v5", 123) == "pk"
        assert _classify_strength("3v5", 123) == "pk"
        assert _classify_strength("3v4", 123) == "pk"

    def test_empty_net(self):
        assert _classify_strength("5v5", None) == "en"
        assert _classify_strength("5v4", None) == "en"

    def test_other(self):
        assert _classify_strength("4v4", 123) == "other"
        assert _classify_strength("3v3", 123) == "other"

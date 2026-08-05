"""Tests for linemate quality and opportunity feature metrics."""

import pytest

from src.analytics.rapm.metrics import (
    _compute_linemate_quality,
    _compute_elevator_nearby,
    OpportunityFeatures,
)


def _seg(home, away, duration):
    return {
        "game_id": 1,
        "duration_seconds": duration,
        "home_skater_ids": home,
        "away_skater_ids": away,
    }


class TestComputeLinemateQuality:
    def test_basic(self):
        ratings = {1: 1.0, 2: 0.5, 3: 0.5, 4: 0.5, 5: 0.5}
        segments = [_seg([1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60)]
        lq = _compute_linemate_quality(segments, 1, ratings)
        assert abs(lq - 0.5) < 0.001

    def test_weighted_by_duration(self):
        ratings = {1: 1.0, 2: 0.8, 3: 0.2, 10: 0.5, 11: 0.5, 12: 0.5}
        segments = [
            _seg([1, 2, 10, 11, 12], [6, 7, 8, 9, 20], 180),
            _seg([1, 3, 10, 11, 12], [6, 7, 8, 9, 20], 60),
        ]
        lq = _compute_linemate_quality(segments, 1, ratings)
        # 180s with avg(0.8, 0.5, 0.5, 0.5)=0.575 + 60s with avg(0.2, 0.5, 0.5, 0.5)=0.425
        expected = (0.575 * 180 + 0.425 * 60) / 240
        assert abs(lq - expected) < 0.001

    def test_away_team(self):
        ratings = {1: 1.0, 6: 0.3, 7: 0.3, 8: 0.3, 9: 0.3}
        segments = [_seg([2, 3, 4, 5, 10], [1, 6, 7, 8, 9], 60)]
        lq = _compute_linemate_quality(segments, 1, ratings)
        assert abs(lq - 0.3) < 0.001

    def test_unrated_teammates_excluded(self):
        ratings = {1: 1.0, 2: 0.6}
        segments = [_seg([1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60)]
        lq = _compute_linemate_quality(segments, 1, ratings)
        # Only player 2 is rated among teammates
        assert abs(lq - 0.6) < 0.001

    def test_no_rated_teammates(self):
        ratings = {1: 1.0}
        segments = [_seg([1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60)]
        lq = _compute_linemate_quality(segments, 1, ratings)
        assert lq is None

    def test_empty_segments(self):
        ratings = {1: 1.0}
        lq = _compute_linemate_quality([], 1, ratings)
        assert lq is None

    def test_player_not_in_segment(self):
        ratings = {1: 1.0, 2: 0.5}
        segments = [_seg([2, 3, 4, 5, 6], [7, 8, 9, 10, 11], 60)]
        lq = _compute_linemate_quality(segments, 1, ratings)
        assert lq is None


class TestComputeElevatorNearby:
    def test_basic(self):
        elevations = {2: 0.3, 3: 0.1, 4: -0.05}
        segments = [_seg([1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60)]
        result = _compute_elevator_nearby(segments, 1, elevations)
        assert abs(result - 0.3) < 0.001

    def test_no_elevation_data(self):
        elevations = {}
        segments = [_seg([1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60)]
        result = _compute_elevator_nearby(segments, 1, elevations)
        assert result is None

    def test_across_multiple_segments(self):
        elevations = {2: 0.1, 6: 0.5}
        segments = [
            _seg([1, 2, 3, 4, 5], [6, 7, 8, 9, 10], 60),
            _seg([6, 7, 8, 9, 10], [1, 2, 3, 4, 5], 60),
        ]
        # Player 1 is away in segment 2, teammates include player 2
        # Player 1 is home in segment... wait no. Player 1 is home in seg 0, away in seg 1.
        # Home seg 0: teammates 2,3,4,5 → elev for 2 = 0.1
        # Away seg 1: teammates 2,3,4,5 → elev for 2 = 0.1
        # Player 6 is never a teammate of player 1 (always opposing)
        result = _compute_elevator_nearby(segments, 1, elevations)
        assert abs(result - 0.1) < 0.001


@pytest.mark.integration
class TestOpportunityFeaturesIntegration:
    def test_mcdavid(self):
        from src.core.db import get_session
        from src.analytics.rapm.metrics import opportunity_features
        from datetime import date

        with get_session() as session:
            features = opportunity_features(
                session, player_id=8478402, as_of=date(2026, 4, 1)
            )
            assert features.linemate_quality is not None
            assert features.own_rating is not None
            assert features.own_rating > 0.5
            assert features.deployment_gap is not None

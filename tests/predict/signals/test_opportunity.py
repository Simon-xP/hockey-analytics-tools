"""Tests for opportunity scoring, including RAPM-derived linemate component."""

from unittest.mock import patch, MagicMock
import pytest

from src.analytics.rapm.metrics import OpportunityFeatures
from src.predict.signals.opportunity import _linemate_opportunity_score


class TestLinemateOpportunityScore:
    """Unit tests for the RAPM-derived linemate opportunity component."""

    def _mock_features(self, **overrides):
        defaults = {
            "linemate_quality": 0.5,
            "linemate_quality_20g": 0.4,
            "linemate_quality_delta": 0.1,
            "own_rating": 0.3,
            "deployment_gap": 0.2,
            "elevator_nearby": 0.3,
        }
        defaults.update(overrides)
        return OpportunityFeatures(**defaults)

    @patch("src.predict.signals.opportunity.opportunity_features")
    def test_positive_all_signals(self, mock_feat):
        mock_feat.return_value = self._mock_features(
            linemate_quality_delta=0.2,
            deployment_gap=0.3,
            elevator_nearby=0.4,
        )
        session = MagicMock()
        score = _linemate_opportunity_score(session, 1, None)
        assert score > 0
        # delta: min(0.10, 0.2*0.5)=0.10
        # gap: min(0.10, 0.3*0.3)=0.09
        # elevator: min(0.10, 0.4*0.2)=0.08
        assert abs(score - 0.25) < 0.02  # near the cap

    @patch("src.predict.signals.opportunity.opportunity_features")
    def test_negative_signals(self, mock_feat):
        mock_feat.return_value = self._mock_features(
            linemate_quality_delta=-0.3,
            deployment_gap=-0.4,
            elevator_nearby=0.0,
        )
        session = MagicMock()
        score = _linemate_opportunity_score(session, 1, None)
        assert score < 0

    @patch("src.predict.signals.opportunity.opportunity_features")
    def test_all_none_returns_zero(self, mock_feat):
        mock_feat.return_value = OpportunityFeatures(
            linemate_quality=None,
            linemate_quality_20g=None,
            linemate_quality_delta=None,
            own_rating=None,
            deployment_gap=None,
            elevator_nearby=None,
        )
        session = MagicMock()
        score = _linemate_opportunity_score(session, 1, None)
        assert score == 0.0

    @patch("src.predict.signals.opportunity.opportunity_features")
    def test_clamped_to_bounds(self, mock_feat):
        mock_feat.return_value = self._mock_features(
            linemate_quality_delta=10.0,
            deployment_gap=10.0,
            elevator_nearby=10.0,
        )
        session = MagicMock()
        score = _linemate_opportunity_score(session, 1, None)
        assert score <= 0.25

        mock_feat.return_value = self._mock_features(
            linemate_quality_delta=-10.0,
            deployment_gap=-10.0,
            elevator_nearby=0.0,
        )
        score = _linemate_opportunity_score(session, 1, None)
        assert score >= -0.25

    @patch("src.predict.signals.opportunity.opportunity_features")
    def test_elevator_only_contributes_when_positive(self, mock_feat):
        mock_feat.return_value = self._mock_features(
            linemate_quality_delta=0.0,
            deployment_gap=0.0,
            elevator_nearby=-0.1,
        )
        session = MagicMock()
        score = _linemate_opportunity_score(session, 1, None)
        assert score == 0.0

    @patch("src.predict.signals.opportunity.opportunity_features")
    def test_only_delta_signal(self, mock_feat):
        mock_feat.return_value = self._mock_features(
            linemate_quality_delta=0.1,
            deployment_gap=None,
            elevator_nearby=None,
        )
        session = MagicMock()
        score = _linemate_opportunity_score(session, 1, None)
        # 0.1 * 0.5 = 0.05
        assert abs(score - 0.05) < 0.001

    @patch("src.predict.signals.opportunity.opportunity_features")
    def test_as_of_passed_through(self, mock_feat):
        from datetime import date
        mock_feat.return_value = self._mock_features()
        session = MagicMock()
        cutoff = date(2026, 3, 15)
        _linemate_opportunity_score(session, 42, cutoff)
        mock_feat.assert_called_once_with(session, 42, as_of=cutoff)

    def test_rapm_unavailable_returns_zero(self):
        session = MagicMock()
        with patch(
            "src.predict.signals.opportunity.opportunity_features",
            side_effect=Exception("no table"),
        ):
            score = _linemate_opportunity_score(session, 1, None)
            assert score == 0.0

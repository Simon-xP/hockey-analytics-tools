"""Tests for transaction scoring logic."""

from datetime import date

from src.optimize.models import (
    AggressionLevel,
    PlayerType,
    PlayerValue,
    ReplacementLevel,
)
from src.optimize.week.heavy import score_transaction


def _make_player_value(
    fpts_per_game: float = 3.0,
    fillable_games: int = 3,
    positions: list[str] | None = None,
    upside_score: float = 0.0,
    position_scarcity: float = 0.0,
    **kwargs,
) -> PlayerValue:
    defaults = dict(
        nhl_id=1,
        name="Test Player",
        team="TOR",
        positions=positions or ["C", "LW"],
        player_type=PlayerType.SKATER,
        fpts_per_game=fpts_per_game,
        games_in_window=fillable_games,
        fillable_games=fillable_games,
        window_fpts=fpts_per_game * fillable_games,
        upside_score=upside_score,
        position_scarcity=position_scarcity,
    )
    defaults.update(kwargs)
    return PlayerValue(**defaults)


def _make_replacement() -> ReplacementLevel:
    return ReplacementLevel(
        forward=2.0,
        defense=1.5,
        computed_at=date(2026, 4, 1),
    )


class TestScoreTransaction:
    def test_positive_quality_delta_positive_score(self):
        add = _make_player_value(fpts_per_game=5.0, fillable_games=3)
        drop = _make_player_value(fpts_per_game=2.0, fillable_games=3)
        score, reasoning = score_transaction(add, drop, _make_replacement())
        assert score > 0

    def test_negative_quality_delta_negative_score(self):
        add = _make_player_value(fpts_per_game=1.0, fillable_games=3)
        drop = _make_player_value(fpts_per_game=4.0, fillable_games=3)
        score, reasoning = score_transaction(add, drop, _make_replacement())
        assert score < 0

    def test_equal_players_near_zero(self):
        add = _make_player_value(fpts_per_game=3.0, fillable_games=3)
        drop = _make_player_value(fpts_per_game=3.0, fillable_games=3)
        score, reasoning = score_transaction(add, drop, _make_replacement())
        assert abs(score) < 0.5

    def test_aggressive_weighs_schedule_more(self):
        add = _make_player_value(fpts_per_game=2.0, fillable_games=4)
        drop = _make_player_value(fpts_per_game=3.0, fillable_games=2)
        score_normal, _ = score_transaction(
            add, drop, _make_replacement(), AggressionLevel.NORMAL
        )
        score_aggressive, _ = score_transaction(
            add, drop, _make_replacement(), AggressionLevel.AGGRESSIVE
        )
        assert score_aggressive > score_normal

    def test_conservative_weighs_quality_more(self):
        add = _make_player_value(fpts_per_game=5.0, fillable_games=2)
        drop = _make_player_value(fpts_per_game=2.0, fillable_games=4)
        score_conservative, _ = score_transaction(
            add, drop, _make_replacement(), AggressionLevel.CONSERVATIVE
        )
        score_desperate, _ = score_transaction(
            add, drop, _make_replacement(), AggressionLevel.DESPERATE
        )
        assert score_conservative > score_desperate

    def test_more_drop_games_penalizes(self):
        add = _make_player_value(fpts_per_game=3.0, fillable_games=2)
        drop = _make_player_value(fpts_per_game=3.0, fillable_games=4)
        score, _ = score_transaction(add, drop, _make_replacement())
        assert score < 0

    def test_scarcity_penalty_applied(self):
        add = _make_player_value(fpts_per_game=3.5, fillable_games=3)
        drop_scarce = _make_player_value(
            fpts_per_game=3.0, fillable_games=3, position_scarcity=0.8,
        )
        drop_common = _make_player_value(
            fpts_per_game=3.0, fillable_games=3, position_scarcity=0.0,
        )
        score_scarce, _ = score_transaction(add, drop_scarce, _make_replacement())
        score_common, _ = score_transaction(add, drop_common, _make_replacement())
        assert score_scarce < score_common

    def test_upside_bonus_helps_add(self):
        add_upside = _make_player_value(fpts_per_game=3.0, fillable_games=3, upside_score=1.0)
        add_no_upside = _make_player_value(fpts_per_game=3.0, fillable_games=3, upside_score=0.0)
        drop = _make_player_value(fpts_per_game=3.0, fillable_games=3)
        score_with, _ = score_transaction(add_upside, drop, _make_replacement())
        score_without, _ = score_transaction(add_no_upside, drop, _make_replacement())
        assert score_with > score_without

    def test_opportunity_bonus_helps_add(self):
        add_opp = _make_player_value(fpts_per_game=3.0, fillable_games=3, opportunity_score=1.0)
        add_no_opp = _make_player_value(fpts_per_game=3.0, fillable_games=3, opportunity_score=0.0)
        drop = _make_player_value(fpts_per_game=3.0, fillable_games=3)
        score_with, _ = score_transaction(add_opp, drop, _make_replacement())
        score_without, _ = score_transaction(add_no_opp, drop, _make_replacement())
        assert score_with > score_without

    def test_upside_and_opportunity_are_additive(self):
        add_both = _make_player_value(fpts_per_game=3.0, fillable_games=3, upside_score=1.0, opportunity_score=1.0)
        add_upside_only = _make_player_value(fpts_per_game=3.0, fillable_games=3, upside_score=1.0, opportunity_score=0.0)
        drop = _make_player_value(fpts_per_game=3.0, fillable_games=3)
        score_both, _ = score_transaction(add_both, drop, _make_replacement())
        score_upside, _ = score_transaction(add_upside_only, drop, _make_replacement())
        assert score_both > score_upside

    def test_reasoning_not_empty(self):
        add = _make_player_value()
        drop = _make_player_value()
        _, reasoning = score_transaction(add, drop, _make_replacement())
        assert len(reasoning) >= 3
        assert any("Quality" in r for r in reasoning)
        assert any("Schedule" in r for r in reasoning)

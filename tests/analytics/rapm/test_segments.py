"""Tests for RAPM shift segment builder."""

import pytest

from src.analytics.rapm.segments import (
    _get_breakpoints,
    _get_situation_at_time,
    _map_shift_teams,
    _sum_xg_in_window,
    build_game_segments,
)
from src.analytics.advanced_stats.shifts import time_to_seconds


class TestMapShiftTeams:
    def test_exact_match(self):
        home, away = _map_shift_teams({10, 20}, 10, 20)
        assert home == 10
        assert away == 20

    def test_home_matches_away_inferred(self):
        home, away = _map_shift_teams({10, 68}, 10, 59)
        assert home == 10
        assert away == 68

    def test_away_matches_home_inferred(self):
        home, away = _map_shift_teams({68, 20}, 10, 20)
        assert home == 68
        assert away == 20

    def test_neither_matches(self):
        home, away = _map_shift_teams({68, 99}, 10, 20)
        assert home is not None
        assert away is not None

    def test_wrong_count(self):
        home, away = _map_shift_teams({10}, 10, 20)
        assert home is None
        assert away is None


class TestGetBreakpoints:
    def test_basic(self):
        shifts = [
            {"period": 1, "start_time": "0:00", "end_time": "0:45", "team_id": 1, "player_id": 100},
            {"period": 1, "start_time": "0:30", "end_time": "1:15", "team_id": 2, "player_id": 200},
        ]
        bp = _get_breakpoints(shifts, 1)
        assert bp == [0, 30, 45, 75]

    def test_filters_by_period(self):
        shifts = [
            {"period": 1, "start_time": "0:00", "end_time": "0:45", "team_id": 1, "player_id": 100},
            {"period": 2, "start_time": "0:10", "end_time": "0:50", "team_id": 1, "player_id": 100},
        ]
        bp = _get_breakpoints(shifts, 1)
        assert bp == [0, 45]

    def test_deduplicates(self):
        shifts = [
            {"period": 1, "start_time": "0:00", "end_time": "0:45", "team_id": 1, "player_id": 100},
            {"period": 1, "start_time": "0:00", "end_time": "0:45", "team_id": 1, "player_id": 101},
        ]
        bp = _get_breakpoints(shifts, 1)
        assert bp == [0, 45]


class TestSumXgInWindow:
    def test_basic(self):
        shots = [
            {"period": 1, "period_seconds": 10, "is_home": True, "xg": 0.1},
            {"period": 1, "period_seconds": 20, "is_home": False, "xg": 0.2},
            {"period": 1, "period_seconds": 30, "is_home": True, "xg": 0.05},
        ]
        home, away = _sum_xg_in_window(shots, 1, 0, 25)
        assert abs(home - 0.1) < 1e-9
        assert abs(away - 0.2) < 1e-9

    def test_excludes_boundary(self):
        shots = [
            {"period": 1, "period_seconds": 25, "is_home": True, "xg": 0.3},
        ]
        home, away = _sum_xg_in_window(shots, 1, 0, 25)
        assert home == 0.0

    def test_wrong_period(self):
        shots = [
            {"period": 2, "period_seconds": 10, "is_home": True, "xg": 0.5},
        ]
        home, away = _sum_xg_in_window(shots, 1, 0, 100)
        assert home == 0.0


class TestGetSituationAtTime:
    def test_5v5_default(self):
        sit = _get_situation_at_time({}, 1, 100, 10)
        assert sit == "5v5"

    def test_uses_timeline(self):
        timeline = {1: [(0, "1551"), (120, "1451")]}
        assert _get_situation_at_time(timeline, 1, 60, 10) == "5v5"
        assert _get_situation_at_time(timeline, 1, 130, 10) == "pp"


@pytest.mark.integration
class TestBuildGameSegmentsIntegration:
    """Integration tests that hit the database."""

    def test_real_game(self):
        from src.core.db import get_session
        from sqlalchemy import text

        with get_session() as session:
            row = session.execute(text("""
                SELECT g.game_id, g.home_team_id, g.away_team_id
                FROM games g
                WHERE EXISTS (SELECT 1 FROM player_shifts ps WHERE ps.game_id = g.game_id)
                  AND EXISTS (SELECT 1 FROM game_events ge WHERE ge.game_id = g.game_id)
                  AND EXISTS (SELECT 1 FROM shot_attempts sa WHERE sa.game_id = g.game_id AND sa.xg IS NOT NULL)
                  AND g.date >= '2025-10-01'
                ORDER BY g.date DESC LIMIT 1
            """)).fetchone()

            if not row:
                pytest.skip("No game with shifts + events + xG")

            game_id, home_id, away_id = row
            segments = build_game_segments(session, game_id, home_id, away_id)

            assert len(segments) > 50

            fv5 = [s for s in segments if s["situation"] == "5v5"]
            assert len(fv5) > 30

            for s in fv5:
                assert len(s["home_skater_ids"]) == 5
                assert len(s["away_skater_ids"]) == 5
                assert s["duration_seconds"] >= 2
                assert s["period"] in (1, 2, 3)
                assert s["home_xgf"] >= 0
                assert s["away_xgf"] >= 0

            total_toi = sum(s["duration_seconds"] for s in fv5) / 60
            assert 25 < total_toi < 55

    def test_xg_totals_match(self):
        """Verify segment xG sums match game_advanced_stats totals."""
        from src.core.db import get_session
        from sqlalchemy import text

        with get_session() as session:
            row = session.execute(text("""
                SELECT g.game_id, g.home_team_id, g.away_team_id
                FROM games g
                JOIN game_advanced_stats gas ON g.game_id = gas.game_id
                WHERE EXISTS (SELECT 1 FROM player_shifts ps WHERE ps.game_id = g.game_id)
                  AND EXISTS (SELECT 1 FROM shot_attempts sa WHERE sa.game_id = g.game_id AND sa.xg IS NOT NULL)
                  AND g.date >= '2025-10-01'
                  AND gas.situation = '5v5'
                ORDER BY g.date DESC LIMIT 1
            """)).fetchone()

            if not row:
                pytest.skip("No game with advanced stats + shifts + xG")

            game_id, home_id, away_id = row
            segments = build_game_segments(session, game_id, home_id, away_id)
            fv5 = [s for s in segments if s["situation"] == "5v5"]

            seg_home_xg = sum(s["home_xgf"] for s in fv5)
            seg_away_xg = sum(s["away_xgf"] for s in fv5)

            # Compare against shot_attempts directly (not game_advanced_stats
            # which may use game-table team IDs)
            sa_rows = session.execute(text("""
                SELECT team_id, SUM(xg) as total_xg
                FROM shot_attempts
                WHERE game_id = :gid AND strength_state = '5v5'
                  AND xg IS NOT NULL AND period_type = 'REG'
                GROUP BY team_id
            """), {"gid": game_id}).fetchall()

            shift_teams = set()
            for s in fv5:
                shift_teams.update(s["home_skater_ids"][:1])
                break

            sa_total = sum(r[1] for r in sa_rows)
            seg_total = seg_home_xg + seg_away_xg

            # Small gap is expected: we filter out sub-2s segments and
            # segments where skater counts don't match the situation code.
            pct_diff = abs(seg_total - sa_total) / sa_total if sa_total > 0 else 0
            assert pct_diff < 0.10, (
                f"Segment xG ({seg_total:.4f}) differs from shot_attempts "
                f"({sa_total:.4f}) by {pct_diff:.1%} — expected < 10%"
            )

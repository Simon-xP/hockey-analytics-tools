"""Tests for game log scraper functions (no network calls)."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src.ingest.natural_stat_trick.scraper import (
    ScrapeBudget,
    build_game_log_url,
)


class TestBuildGameLogUrl:
    def test_basic_url(self):
        url = build_game_log_url("20242025", 8478402, "5v5", "std")
        assert "playerreport.php" in url
        assert "fromseason=20242025" in url
        assert "thruseason=20242025" in url
        assert "stype=2" in url
        assert "sit=5v5" in url
        assert "stdoi=std" in url
        assert "rate=y" in url
        assert "v=g" in url
        assert "playerid=8478402" in url

    def test_all_situations(self):
        url = build_game_log_url("20242025", 8478402, "all", "std")
        assert "sit=all" in url

    def test_on_ice(self):
        url = build_game_log_url("20242025", 8478402, "5v5", "oi")
        assert "stdoi=oi" in url


class TestScrapeBudget:
    def test_new_state(self, tmp_path):
        state_file = tmp_path / "state.json"
        budget = ScrapeBudget(state_path=state_file)
        assert budget.requests_today() == 0
        assert budget.can_request(100, run_count=0)

    def test_increment(self, tmp_path):
        state_file = tmp_path / "state.json"
        budget = ScrapeBudget(state_path=state_file)
        budget.increment()
        assert budget.requests_today() == 1
        budget.increment()
        assert budget.requests_today() == 2

    def test_budget_limit(self, tmp_path):
        state_file = tmp_path / "state.json"
        budget = ScrapeBudget(state_path=state_file)
        assert budget.can_request(5, run_count=5) is False
        assert budget.can_request(6, run_count=5) is True

    def test_persistence(self, tmp_path):
        state_file = tmp_path / "state.json"
        budget1 = ScrapeBudget(state_path=state_file)
        budget1.increment()
        budget1.increment()

        # Load from same file
        budget2 = ScrapeBudget(state_path=state_file)
        assert budget2.requests_today() == 2

    def test_mark_player_done(self, tmp_path):
        state_file = tmp_path / "state.json"
        budget = ScrapeBudget(state_path=state_file)

        assert not budget.is_player_done("20242025", 8478402, "5v5", "std")
        budget.mark_player_done("20242025", 8478402, "5v5", "std")
        assert budget.is_player_done("20242025", 8478402, "5v5", "std")
        assert not budget.is_player_done("20242025", 8478402, "5v5", "oi")
        assert not budget.is_player_done("20242025", 8478403, "5v5", "std")

    def test_get_progress(self, tmp_path):
        state_file = tmp_path / "state.json"
        budget = ScrapeBudget(state_path=state_file)
        budget.mark_player_done("20242025", 1, "5v5", "std")
        budget.mark_player_done("20242025", 2, "5v5", "std")
        budget.mark_player_done("20242025", 1, "all", "oi")

        progress = budget.get_progress("20242025")
        assert progress["20242025_5v5_std"] == 2
        assert progress["20242025_all_oi"] == 1

    def test_no_duplicate_marks(self, tmp_path):
        state_file = tmp_path / "state.json"
        budget = ScrapeBudget(state_path=state_file)
        budget.mark_player_done("20242025", 1, "5v5", "std")
        budget.mark_player_done("20242025", 1, "5v5", "std")  # duplicate

        progress = budget.get_progress("20242025")
        assert progress["20242025_5v5_std"] == 1

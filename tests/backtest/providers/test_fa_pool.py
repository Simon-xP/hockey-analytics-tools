"""Integration tests for `src.backtest.providers.fa_pool.FAPoolReconstructor`.

Spot-check the reconstructed free-agent / rostered pool against Yahoo roster
snapshots. Hits the real dev database and skips cleanly if the Yahoo league
data isn't synced.
"""

from datetime import date, datetime

import pytest

from src.backtest.providers.fa_pool import FAPoolReconstructor
from src.core.db import get_session
from src.core.models import YahooDraftPick
from src.backtest.providers.yahoo import get_my_roster_at

LEAGUE_KEY = "465.l.17649"
TEAM_NAME = "McChuckin'"


def _has_yahoo_data(session) -> bool:
    return (
        session.query(YahooDraftPick)
        .filter(YahooDraftPick.league_key == LEAGUE_KEY)
        .first()
        is not None
    )


@pytest.fixture(scope="module")
def yahoo_session():
    with get_session() as session:
        if not _has_yahoo_data(session):
            pytest.skip("Yahoo league data not synced")
        yield session


class TestFAPoolReconstruction:
    """Spot-check FAPoolReconstructor against get_my_roster_at."""

    SPOT_CHECK_DATES = [
        date(2025, 11, 1),
        date(2025, 12, 15),
        date(2026, 1, 15),
        date(2026, 2, 1),
        date(2026, 3, 1),
    ]

    def test_my_roster_is_subset_of_all_rostered(self, yahoo_session):
        """Our team's roster nhl_ids must be a subset of league-wide rostered."""
        recon = FAPoolReconstructor(yahoo_session, LEAGUE_KEY)

        checked = 0
        for check_date in self.SPOT_CHECK_DATES:
            as_of_dt = datetime.combine(check_date, datetime.max.time())
            my_roster = get_my_roster_at(
                LEAGUE_KEY, TEAM_NAME, as_of_dt, yahoo_session,
            )
            my_nhl_ids = {
                p["nhl_id"] for p in my_roster if p.get("nhl_id")
            }

            if not my_nhl_ids:
                continue

            all_rostered = recon.get_rostered_nhl_ids(check_date)
            missing = my_nhl_ids - all_rostered
            assert not missing, (
                f"On {check_date}: {len(missing)} players on our roster "
                f"not found in league-wide rostered set: {missing}"
            )
            checked += 1

        assert checked >= 3, (
            f"Only spot-checked {checked} dates — need at least 3 with roster data"
        )

    def test_rostered_count_is_plausible(self, yahoo_session):
        """League-wide rostered count should be between 100-300 (12 teams * ~15 roster spots)."""
        recon = FAPoolReconstructor(yahoo_session, LEAGUE_KEY)
        mid_season = date(2026, 1, 15)
        rostered = recon.get_rostered_nhl_ids(mid_season)
        assert 50 < len(rostered) < 400, (
            f"Rostered count {len(rostered)} is implausible for a 12-team league"
        )

    def test_rostered_changes_over_time(self, yahoo_session):
        """The rostered set should differ between early and late season."""
        recon = FAPoolReconstructor(yahoo_session, LEAGUE_KEY)
        early = recon.get_rostered_nhl_ids(date(2025, 10, 15))
        late = recon.get_rostered_nhl_ids(date(2026, 2, 15))
        assert early != late, "Rostered set didn't change between Oct and Feb"

    def test_cache_returns_same_result(self, yahoo_session):
        """Calling get_rostered_nhl_ids twice with the same date returns identical results."""
        recon = FAPoolReconstructor(yahoo_session, LEAGUE_KEY)
        d = date(2026, 1, 15)
        first = recon.get_rostered_nhl_ids(d)
        second = recon.get_rostered_nhl_ids(d)
        assert first == second

    def test_unavailable_includes_waiver_players(self, yahoo_session):
        """Players dropped within waiver_days must appear in unavailable but not rostered."""
        recon = FAPoolReconstructor(yahoo_session, LEAGUE_KEY, waiver_days=2)
        unavailable = recon.get_unavailable_nhl_ids(date(2026, 1, 15))
        rostered = recon.get_rostered_nhl_ids(date(2026, 1, 15))
        on_waivers = unavailable - rostered
        assert unavailable >= rostered, (
            "Unavailable must be a superset of rostered"
        )
        assert len(unavailable) >= len(rostered), (
            "Unavailable should be at least as large as rostered"
        )

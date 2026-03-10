"""Feature extractors for the forecasting module.

Each extractor follows a simple protocol: extract(session, feature_set, before_date)
adds keys to feature_set.features dict. New extractors slot in without modifying
existing code.

Game-level stats are per-60 rates (from NST rate=y). Feature names reflect this:
  rolling_5_goals_per_60, season_avg_ixg_per_60, etc.

Season aggregate stats (SeasonStats) are raw counts, so we compute per-game rates
ourselves. Feature names reflect this: prior_season_goals_per_gp, etc.
"""

from datetime import date
from typing import Protocol

from sqlalchemy.orm import Session

from src.core.models import (
    GameIndividualStats,
    GameOnIceStats,
    OnIceStats,
    SeasonStats,
)
from src.tools.forecasting.models import FeatureSet


class FeatureExtractor(Protocol):
    """Protocol for feature extractors."""

    def extract(self, session: Session, fs: FeatureSet, before_date: date) -> None:
        """Add features to fs.features dict using only data from before before_date."""
        ...


# =============================================================================
# ROLLING INDIVIDUAL STATS (per-60 rates from GameIndividualStats)
# =============================================================================

# DB column name -> feature suffix
# These are all per-60 rate columns from GameIndividualStats
INDIVIDUAL_RATE_STATS = {
    "goals_per_60": "goals_per_60",
    "total_assists_per_60": "assists_per_60",
    "shots_per_60": "shots_per_60",
    "ixg_per_60": "ixg_per_60",
    "icf_per_60": "icf_per_60",
    "iscf_per_60": "iscf_per_60",
    "hits_per_60": "hits_per_60",
    "shots_blocked_per_60": "blocked_per_60",
}

# Non-rate columns (raw values per game)
INDIVIDUAL_OTHER_STATS = {
    "toi": "toi",
    "sh_pct": "sh_pct",
    "ipp": "ipp",
}


class RollingIndividualExtractor:
    """Compute rolling averages of individual per-game stats (per-60 rates).

    Adds features like:
      rolling_5_goals_per_60, rolling_5_ixg_per_60
      season_avg_goals_per_60, season_avg_toi, etc.
    """

    def __init__(
        self,
        windows: list[int] = None,
        situation: str = "all",
    ):
        self.windows = windows or [5]
        self.situation = situation

    def extract(self, session: Session, fs: FeatureSet, before_date: date) -> None:
        prior_stats = (
            session.query(GameIndividualStats)
            .filter(
                GameIndividualStats.nhl_id == fs.nhl_id,
                GameIndividualStats.situation == self.situation,
                GameIndividualStats.game_date < before_date,
            )
            .order_by(GameIndividualStats.game_date.desc())
            .all()
        )

        if not prior_stats:
            return

        all_stats = {**INDIVIDUAL_RATE_STATS, **INDIVIDUAL_OTHER_STATS}
        max_window = max(self.windows)
        recent = prior_stats[:max_window]

        fs.features["season_gp"] = len(prior_stats)

        # Season average (all prior games)
        for col, suffix in all_stats.items():
            values = [
                getattr(s, col) for s in prior_stats
                if getattr(s, col) is not None
            ]
            if values:
                fs.features[f"season_avg_{suffix}"] = sum(values) / len(values)

        # Rolling window averages
        for window in self.windows:
            window_stats = recent[:window]
            if len(window_stats) < window:
                continue

            for col, suffix in all_stats.items():
                values = [
                    getattr(s, col) for s in window_stats
                    if getattr(s, col) is not None
                ]
                if values:
                    fs.features[f"rolling_{window}_{suffix}"] = (
                        sum(values) / len(values)
                    )


# =============================================================================
# ROLLING ON-ICE STATS (per-60 rates from GameOnIceStats)
# =============================================================================

# Per-60 rate columns
ON_ICE_RATE_STATS = {
    "cf_per_60": "cf_per_60",
    "scf_per_60": "scf_per_60",
    "xgf_per_60": "xgf_per_60",
}

# Percentage / non-rate columns
ON_ICE_PCT_STATS = {
    "on_ice_sh_pct": "sh_pct",
    "off_zone_start_pct": "ozs_pct",
}


class RollingOnIceExtractor:
    """Compute rolling averages of on-ice per-game stats.

    Adds features like:
      rolling_5_oi_cf_per_60, rolling_5_oi_sh_pct, etc.
      season_avg_oi_cf_per_60, etc.
    """

    def __init__(
        self,
        windows: list[int] = None,
        situation: str = "all",
    ):
        self.windows = windows or [5]
        self.situation = situation

    def extract(self, session: Session, fs: FeatureSet, before_date: date) -> None:
        prior_stats = (
            session.query(GameOnIceStats)
            .filter(
                GameOnIceStats.nhl_id == fs.nhl_id,
                GameOnIceStats.situation == self.situation,
                GameOnIceStats.game_date < before_date,
            )
            .order_by(GameOnIceStats.game_date.desc())
            .all()
        )

        if not prior_stats:
            return

        all_stats = {**ON_ICE_RATE_STATS, **ON_ICE_PCT_STATS}
        max_window = max(self.windows)
        recent = prior_stats[:max_window]

        # Season average
        for col, suffix in all_stats.items():
            values = [
                getattr(s, col) for s in prior_stats
                if getattr(s, col) is not None
            ]
            if values:
                fs.features[f"season_avg_oi_{suffix}"] = (
                    sum(values) / len(values)
                )

        # Rolling window averages
        for window in self.windows:
            window_stats = recent[:window]
            if len(window_stats) < window:
                continue

            for col, suffix in all_stats.items():
                values = [
                    getattr(s, col) for s in window_stats
                    if getattr(s, col) is not None
                ]
                if values:
                    fs.features[f"rolling_{window}_oi_{suffix}"] = (
                        sum(values) / len(values)
                    )


# =============================================================================
# SEASON AGGREGATE (prior completed seasons — raw counts, we compute per-GP)
# =============================================================================

# SeasonStats columns that are raw counts — we divide by GP to get per-game
SEASON_COUNT_COLS = {
    "goals": "goals_per_gp",
    "total_assists": "assists_per_gp",
    "shots": "shots_per_gp",
    "hits": "hits_per_gp",
    "shots_blocked": "blocked_per_gp",
    "pim": "pim_per_gp",
}


class SeasonAggregateExtractor:
    """Add features from prior completed season aggregates.

    Uses SeasonStats and OnIceStats from the most recent completed season.
    SeasonStats are raw counts, so we compute per-game rates (not per-60).
    """

    def __init__(self, situation: str = "all_individual_counts"):
        self.situation = situation

    def extract(self, session: Session, fs: FeatureSet, before_date: date) -> None:
        year = before_date.year
        if before_date.month < 10:
            prior_season = f"{year - 2}{year - 1}"
        else:
            prior_season = f"{year - 1}{year}"

        stats = (
            session.query(SeasonStats)
            .filter(
                SeasonStats.nhl_id == fs.nhl_id,
                SeasonStats.season == prior_season,
                SeasonStats.situation == self.situation,
            )
            .first()
        )

        if stats and stats.games_played and stats.games_played > 0:
            gp = stats.games_played
            fs.features["prior_season_gp"] = gp

            for col, suffix in SEASON_COUNT_COLS.items():
                val = getattr(stats, col, None)
                if val is not None:
                    fs.features[f"prior_season_{suffix}"] = val / gp

            if stats.sh_pct is not None:
                fs.features["prior_season_sh_pct"] = stats.sh_pct
            if stats.ipp is not None:
                fs.features["prior_season_ipp"] = stats.ipp

        # On-ice season stats (percentages — no conversion needed)
        oi_situation = self.situation.replace("individual", "on-ice")
        oi_stats = (
            session.query(OnIceStats)
            .filter(
                OnIceStats.nhl_id == fs.nhl_id,
                OnIceStats.season == prior_season,
                OnIceStats.situation == oi_situation,
            )
            .first()
        )

        if oi_stats:
            for col, suffix in ON_ICE_PCT_STATS.items():
                val = getattr(oi_stats, col, None)
                if val is not None:
                    fs.features[f"prior_season_oi_{suffix}"] = val


# =============================================================================
# GAME CONTEXT
# =============================================================================

class GameContextExtractor:
    """Add game-specific context features.

    Features: is_home, is_b2b (back-to-back: 1 day since last game).
    """

    def extract(self, session: Session, fs: FeatureSet, before_date: date) -> None:
        gs = (
            session.query(GameIndividualStats)
            .filter(
                GameIndividualStats.nhl_id == fs.nhl_id,
                GameIndividualStats.game_date == fs.game_date,
            )
            .first()
        )
        if gs and gs.is_home is not None:
            fs.features["is_home"] = 1.0 if gs.is_home else 0.0

        # Back-to-back: did this player play yesterday?
        prior_game_stat = (
            session.query(GameIndividualStats)
            .filter(
                GameIndividualStats.nhl_id == fs.nhl_id,
                GameIndividualStats.game_date < before_date,
            )
            .order_by(GameIndividualStats.game_date.desc())
            .first()
        )

        if prior_game_stat and prior_game_stat.game_date:
            days_rest = (fs.game_date - prior_game_stat.game_date).days
            fs.features["is_b2b"] = 1.0 if days_rest == 1 else 0.0

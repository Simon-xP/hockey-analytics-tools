"""Data models for the matchup state engine."""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class WeekImportance(Enum):
    """How much this week's matchup matters for the season.

    Constrains posture on both axes (see `src/optimize/week/posture.py`):
    - NEUTRAL: punts early, and caps depth at AGGRESSIVE. A week that changes
      nothing is never worth damaging the roster for.
    - BIG: punts only on a blowout, full depth range including DESPERATE.
    - CRAZY: never punts. Playoffs are not conceded at any win probability.
    """

    NEUTRAL = "neutral"
    BIG = "big"
    CRAZY = "crazy"


@dataclass
class MatchupSnapshot:
    """Raw scoreboard data from Yahoo or reconstructed for backtest."""

    my_team_key: str
    opp_team_key: str
    my_earned: float
    opp_earned: float
    week_start: date
    week_end: date
    my_adds_remaining: int
    opp_adds_remaining: int
    yahoo_week: int = 0


@dataclass
class TeamProjection:
    """Remaining-points projection for one team."""

    team_key: str
    earned: float
    mu_remaining: float
    sigma_remaining: float
    remaining_games: int
    remaining_fillable_games: int
    roster_nhl_ids: list[int] = field(default_factory=list)


@dataclass
class PickupBoost:
    """Distribution of points a team can gain from optimal pickups."""

    mu_boost: float
    sigma_boost: float
    n_adds_remaining: int
    top_targets: list[dict] = field(default_factory=list)


@dataclass
class MatchupContext:
    """Full input to the state engine."""

    snapshot: MatchupSnapshot
    my_projection: TeamProjection
    opp_projection: TeamProjection
    my_pickup_boost: PickupBoost
    opp_pickup_boost: PickupBoost
    importance: WeekImportance = WeekImportance.BIG
    my_rank: int = 8
    total_teams: int = 16
    playoff_spots: int = 8
    is_playoff: bool = False


@dataclass
class WinProbability:
    """Result of win probability calculation."""

    p_win: float
    projected_gap: float
    combined_sigma: float
    my_total: float
    opp_total: float
    reasoning: list[str] = field(default_factory=list)

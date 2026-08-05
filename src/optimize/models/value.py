"""Player-level valuation dataclasses.

What a single player is worth over an evaluation window, and the
free-agent baseline they are measured against.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class PlayerType(Enum):
    SKATER = "skater"
    GOALIE = "goalie"


@dataclass
class PlayerValue:
    """Complete valuation of a player for transaction decisions.

    The key number is `window_fpts`: projected fantasy points in the
    evaluation window, considering only games where the player can make
    the active lineup.

    The window can be 1-7 days from the evaluation date. This allows
    finding burst opportunities (e.g., 3 games in 4 days) rather than
    being constrained to arbitrary fantasy week boundaries.
    """

    nhl_id: int
    name: str
    team: str
    positions: list[str]  # Yahoo positions: ["C", "LW"], ["D"], ["G"]
    player_type: PlayerType

    # Core projections
    fpts_per_game: float  # forecast-based FPTS/game (avg across window's games)
    games_in_window: int  # team's total games in the evaluation window
    fillable_games: int  # games where player makes the active lineup
    window_fpts: float  # sum of per-game FPTS for fillable games only

    # Window info
    window_start: Optional[date] = None  # first day of evaluation window
    window_end: Optional[date] = None  # last day of evaluation window
    window_days: int = 7  # length of window in days

    # Context
    avg_toi: float = 0.0  # recent average TOI in minutes
    games_played: int = 0  # season GP (sample size indicator)

    # Adjustments (populated by later milestones)
    upside_score: float = 0.0  # [-1, 1]: individual talent ceiling (see docs/upside-and-opportunity.md)
    opportunity_score: float = 0.0  # [-1, 1]: current situational favorability
    ros_value: float = 0.0  # rest-of-season projected FPTS
    position_scarcity: float = 0.0  # [0, 1]: higher = harder to replace

    # Per-game breakdown (game_date → projected FPTS)
    game_projections: dict[date, float] = field(default_factory=dict)

    # Legacy aliases for backward compatibility
    @property
    def games_this_week(self) -> int:
        return self.games_in_window

    @property
    def weekly_fpts(self) -> float:
        return self.window_fpts


@dataclass
class ReplacementLevel:
    """Replacement level FPTS/game by position group.

    Represents what you can get "for free" from the FA pool at any time.
    No goalie replacement level — goalie value depends on volume (starts),
    not rate (FPTS/start).
    """

    forward: float  # avg FPTS/GP of top-N FA forwards
    defense: float  # avg FPTS/GP of top-N FA defensemen
    computed_at: date
    sample_sizes: dict[str, int] = field(default_factory=dict)

    def for_positions(self, positions: list[str]) -> float:
        """Get replacement level for a player's position group."""
        if "D" in positions:
            return self.defense
        return self.forward


@dataclass
class GoalieStreamScore:
    """Goalie streaming evaluation for a specific game."""

    nhl_id: int
    name: str
    game_date: date
    opponent: str
    opponent_goals_per_game: float  # how many goals the opponent scores (lower = better for goalie)
    opponent_goals_against_per_game: float  # how many goals opponent allows
    starter_confidence: float  # 1.0=confirmed, 0.7=probable, 0.4=unconfirmed
    projected_fpts: float  # estimated fantasy points for this start
    crease_share: float  # 0-1, what % of team's starts this goalie gets
    reasoning: list[str] = field(default_factory=list)

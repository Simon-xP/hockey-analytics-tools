"""Data models for the transaction evaluator.

Core dataclasses used across the transaction evaluation pipeline:
player valuation, drop ranking, transaction scoring, and weekly optimization.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class PlayerType(Enum):
    SKATER = "skater"
    GOALIE = "goalie"


class AggressionLevel(Enum):
    """How aggressively to stream based on matchup context.

    Shifts the weight between short-term (weekly) and long-term (ROS) value
    in transaction scoring.
    """

    CONSERVATIVE = "conservative"  # protect roster, take the L
    NORMAL = "normal"
    AGGRESSIVE = "aggressive"  # close matchup, stream harder
    DESPERATE = "desperate"  # playoffs / must-win


# Aggression → (quality_weight, schedule_weight) for transaction scoring.
#
# Quality weight: how much we value raw player talent (FPTS/GP).
#   A better player is always preferred if quality weight dominates.
# Schedule weight: how much we value short-term games played advantage.
#   More fillable games matter more when we need to win right now.
#
# CONSERVATIVE: prioritize quality — "only add strictly better players"
# DESPERATE:    prioritize schedule — "grab warm bodies who play a lot"
AGGRESSION_WEIGHTS: dict[AggressionLevel, tuple[float, float]] = {
    AggressionLevel.CONSERVATIVE: (0.9, 0.1),
    AggressionLevel.NORMAL: (0.6, 0.4),
    AggressionLevel.AGGRESSIVE: (0.4, 0.6),
    AggressionLevel.DESPERATE: (0.2, 0.8),
}


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
    upside_score: float = 0.0  # [-1, 1]: positive = underperforming talent
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
class TransactionCandidate:
    """A proposed add/drop pair with scored value."""

    add_player: PlayerValue
    drop_player: Optional[PlayerValue]  # None if roster has an open slot
    net_weekly_fpts: float  # add.weekly_fpts - drop.weekly_fpts
    net_ros_value: float  # ROS gain from the swap
    adjusted_score: float  # final score after aggression weighting + adjustments
    reasoning: list[str]  # human-readable explanation of the decision

    def summary(self) -> str:
        drop_name = self.drop_player.name if self.drop_player else "(open slot)"
        return (
            f"ADD {self.add_player.name} ({self.add_player.team}) "
            f"DROP {drop_name} | "
            f"weekly: {self.net_weekly_fpts:+.1f} | "
            f"score: {self.adjusted_score:+.2f}"
        )


@dataclass
class WeekPlan:
    """Optimized set of transactions for a fantasy week."""

    yahoo_week: int
    transactions: list[TransactionCandidate]
    adds_used: int  # out of max (typically 4)
    projected_fpts_gain: float  # total weekly gain from all transactions
    aggression: AggressionLevel
    reasoning: str = ""  # overall rationale

    def summary(self) -> str:
        lines = [
            f"Week {self.yahoo_week} plan ({self.aggression.value}): "
            f"{self.adds_used} adds, {self.projected_fpts_gain:+.1f} projected FPTS",
        ]
        for i, txn in enumerate(self.transactions, 1):
            lines.append(f"  {i}. {txn.summary()}")
        return "\n".join(lines)


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

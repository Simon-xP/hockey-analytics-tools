"""Transaction and week-plan dataclasses.

How aggressively to act, a scored add/drop pair, and the resulting
plan for a fantasy week.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.optimize.models.matchup import PickupBoost, TeamProjection
from src.optimize.models.value import PlayerValue


class AggressionLevel(Enum):
    """How aggressively to stream based on matchup context.

    Shifts the weight between short-term (weekly) and long-term (ROS) value
    in transaction scoring.
    """

    CONSERVATIVE = "conservative"  # protect roster or take the L
    NORMAL = "normal"
    AGGRESSIVE = "aggressive"  # close matchup, stream harder
    DESPERATE = "desperate"  # playoffs / must-win
    # DEPRECATED: superseded by PostureMode.PUNT in src/optimize/models/week.py.
    # Mode and depth are orthogonal (you can punt conservatively or punt
    # aggressively), so "prepare" no longer belongs on this axis. Kept only
    # because src/backtest/ and scripts/run_backtest.py still reference it;
    # P7 removes it. No new code may emit it.
    PREPARE = "prepare"  # optimize for next week, ignore current week value


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
    AggressionLevel.PREPARE: (0.9, 0.1),
}


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
class TeamWeekResult:
    """Result of optimizing a fantasy week for one team.

    Both the light (opponent) and heavy (my team) paths return this, so
    callers can treat any team uniformly. `plan` is only populated by the
    heavy path — the light path models what a team *could* score rather than
    a concrete set of transactions to execute.
    """

    team_key: str
    depth: str  # "light" | "heavy"
    projection: "TeamProjection"
    pickup_boost: "PickupBoost"
    plan: Optional[WeekPlan] = None

    @property
    def expected_total(self) -> float:
        """Points already earned plus projected remaining plus pickup boost."""
        return (
            self.projection.earned
            + self.projection.mu_remaining
            + self.pickup_boost.mu_boost
        )

    def summary(self) -> str:
        head = (
            f"{self.team_key} [{self.depth}]: "
            f"{self.projection.earned:.1f} earned "
            f"+ {self.projection.mu_remaining:.1f} projected "
            f"+ {self.pickup_boost.mu_boost:.1f} pickups "
            f"= {self.expected_total:.1f}"
        )
        if self.plan is None:
            return head
        return head + "\n" + self.plan.summary()

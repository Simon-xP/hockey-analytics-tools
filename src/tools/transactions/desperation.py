"""Desperation / matchup context — determines how aggressively to stream.

Computes an AggressionLevel based on:
1. Current matchup margin (my projected total vs opponent's)
2. Standings position (bubble teams are more desperate)
3. Playoff flag (always desperate in playoffs)
4. Too-far-behind detection (save adds for next week if hopeless)

The aggression level shifts transaction scoring weights between
short-term (weekly) and long-term (ROS) value.
"""

from src.tools.transactions.models import AggressionLevel


def compute_aggression(
    my_projected_total: float,
    opp_projected_total: float,
    my_rank: int,
    total_teams: int = 16,
    playoff_spots: int = 8,
    is_playoff: bool = False,
) -> AggressionLevel:
    """Determine how aggressively to stream based on matchup context.

    Args:
        my_projected_total: My team's projected FPTS for the full week
        opp_projected_total: Opponent's projected FPTS for the full week
        my_rank: Current league standing (1 = first place)
        total_teams: Number of teams in the league
        playoff_spots: Number of teams that make playoffs
        is_playoff: Whether it's fantasy playoffs

    Returns:
        AggressionLevel for transaction scoring.
    """
    if is_playoff:
        return AggressionLevel.DESPERATE

    margin = my_projected_total - opp_projected_total
    on_bubble = my_rank > (playoff_spots - 2)  # within 2 spots of cutoff

    if margin > 30:
        # Cruising — protect the roster
        return AggressionLevel.CONSERVATIVE
    elif margin > 10:
        return AggressionLevel.NORMAL
    elif margin > -10:
        # Close matchup
        if on_bubble:
            return AggressionLevel.DESPERATE  # bubble team in close matchup
        return AggressionLevel.AGGRESSIVE
    elif margin > -40:
        # Behind but catchable
        if on_bubble:
            return AggressionLevel.DESPERATE
        return AggressionLevel.AGGRESSIVE
    else:
        # Too far behind — save adds for next week
        return AggressionLevel.CONSERVATIVE


def compute_aggression_from_yahoo(
    my_score: float,
    opp_score: float,
    my_remaining_projected: float,
    opp_remaining_projected: float,
    my_rank: int,
    total_teams: int = 16,
    playoff_spots: int = 8,
    is_playoff: bool = False,
) -> AggressionLevel:
    """Compute aggression from live Yahoo matchup data.

    Combines already-earned points with projected remaining to get
    full-week projections, then delegates to compute_aggression().

    Args:
        my_score: Points earned so far this week
        opp_score: Opponent points earned so far
        my_remaining_projected: My projected points for remaining games
        opp_remaining_projected: Opponent's projected remaining points
        my_rank: Current league standing
        total_teams: League size
        playoff_spots: Playoff cutoff
        is_playoff: Fantasy playoffs active
    """
    my_total = my_score + my_remaining_projected
    opp_total = opp_score + opp_remaining_projected

    return compute_aggression(
        my_projected_total=my_total,
        opp_projected_total=opp_total,
        my_rank=my_rank,
        total_teams=total_teams,
        playoff_spots=playoff_spots,
        is_playoff=is_playoff,
    )

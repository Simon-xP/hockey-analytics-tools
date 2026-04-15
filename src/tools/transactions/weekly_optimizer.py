"""Weekly transaction optimizer.

Scores individual add/drop transactions and allocates up to 4 adds
across a week to maximize total FPTS gained.

The scoring formula balances short-term (weekly) and long-term (ROS)
value, shifted by aggression level based on matchup context.

The optimizer uses greedy forward search with look-ahead: pick the
best transaction, apply it to the roster, re-score remaining candidates
against updated state, and optionally defer if a better option might
appear later in the week.
"""

from datetime import date

from src.tools.schedule.models import Roster, RosterPlayer
from src.tools.transactions.models import (
    AGGRESSION_WEIGHTS,
    AggressionLevel,
    PlayerValue,
    ReplacementLevel,
    TransactionCandidate,
    WeekPlan,
)


def score_transaction(
    add: PlayerValue,
    drop: PlayerValue,
    replacement: ReplacementLevel,
    aggression: AggressionLevel = AggressionLevel.NORMAL,
) -> tuple[float, list[str]]:
    """Score a single add/drop transaction.

    Returns (score, reasoning) where score > 0 means the add improves
    the team.

    Formula decomposes the swap into two components:
        quality_component  = (add.fpts_per_game - drop.fpts_per_game) * avg_games
        schedule_component = (add.fillable_games - drop.fillable_games) * avg_fpts
        score = w_quality * quality_component + w_schedule * schedule_component
              - scarcity_penalty + upside_bonus

    Aggression shifts the quality/schedule weights:
        CONSERVATIVE: 0.9 / 0.1  (only add strictly better players)
        NORMAL:       0.6 / 0.4
        AGGRESSIVE:   0.4 / 0.6
        DESPERATE:    0.2 / 0.8  (grab warm bodies who play a lot)
    """
    w_quality, w_schedule = AGGRESSION_WEIGHTS[aggression]
    reasoning = []

    quality_delta = add.fpts_per_game - drop.fpts_per_game
    schedule_delta = add.fillable_games - drop.fillable_games
    avg_games = max((add.fillable_games + drop.fillable_games) / 2.0, 1.0)
    avg_fpts = max((add.fpts_per_game + drop.fpts_per_game) / 2.0, 1.0)

    quality_component = quality_delta * avg_games
    schedule_component = schedule_delta * avg_fpts

    reasoning.append(
        f"Quality: {add.fpts_per_game:.2f} - {drop.fpts_per_game:.2f} "
        f"= {quality_delta:+.2f} FPTS/GP × {avg_games:.1f}g = {quality_component:+.1f}"
    )
    reasoning.append(
        f"Schedule: {add.fillable_games} - {drop.fillable_games} "
        f"= {schedule_delta:+d}g × {avg_fpts:.1f} FPTS/GP = {schedule_component:+.1f}"
    )

    # Position scarcity penalty: dropping a scarce position is costly
    scarcity_penalty = drop.position_scarcity * 2.0
    if scarcity_penalty > 0.1:
        reasoning.append(f"Scarcity penalty: -{scarcity_penalty:.1f} (drop has {drop.position_scarcity:.2f} scarcity)")

    # Upside bonus: picking up high-upside players is valuable
    # Penalty for dropping high-upside players
    upside_bonus = add.upside_score * 1.5 - max(0, drop.upside_score) * 1.5
    if abs(upside_bonus) > 0.1:
        reasoning.append(f"Upside adjustment: {upside_bonus:+.1f}")

    # Replacement level context
    repl = replacement.for_positions(drop.positions)
    if drop.fpts_per_game < repl:
        reasoning.append(f"Drop is below replacement ({drop.fpts_per_game:.1f} < {repl:.1f} FPTS/GP)")

    score = (
        w_quality * quality_component
        + w_schedule * schedule_component
        - scarcity_penalty
        + upside_bonus
    )

    reasoning.append(
        f"Score: {score:+.2f} ({aggression.value}: "
        f"{w_quality:.0%} quality / {w_schedule:.0%} schedule)"
    )

    return score, reasoning


def build_candidates(
    add_targets: list[PlayerValue],
    drop_candidates: list[PlayerValue],
    replacement: ReplacementLevel,
    aggression: AggressionLevel = AggressionLevel.NORMAL,
    min_score: float | None = None,
) -> list[TransactionCandidate]:
    """Score all (add, drop) pairs and return sorted candidates.

    Args:
        add_targets: Free agents to consider adding
        drop_candidates: Roster players to consider dropping
        replacement: FA baseline
        aggression: Current aggression level
        min_score: Minimum score to include (None = include all)

    Returns:
        List of TransactionCandidate sorted by adjusted_score descending.
    """
    candidates = []

    for add in add_targets:
        for drop in drop_candidates:
            score, reasoning = score_transaction(add, drop, replacement, aggression)

            if min_score is not None and score < min_score:
                continue

            net_weekly = add.weekly_fpts - drop.weekly_fpts
            net_ros = add.ros_value - drop.ros_value

            candidates.append(
                TransactionCandidate(
                    add_player=add,
                    drop_player=drop,
                    net_weekly_fpts=net_weekly,
                    net_ros_value=net_ros,
                    adjusted_score=score,
                    reasoning=reasoning,
                )
            )

    candidates.sort(key=lambda c: c.adjusted_score, reverse=True)
    return candidates


def _first_game_date(pv: PlayerValue) -> date | None:
    """Get the first game date for a player this week."""
    if not pv.game_projections:
        return None
    return min(pv.game_projections.keys())


def _compute_expected_future_value(
    available_adds: list[PlayerValue],
    top_n: int = 3,
    discount: float = 0.8,
) -> float:
    """Estimate the expected value of future add options.

    Uses the average score of the top-N current free agents,
    discounted by 20% for uncertainty (we can't be sure they'll
    still be available or that the matchups will hold).
    """
    if not available_adds:
        return 0.0

    sorted_adds = sorted(available_adds, key=lambda a: a.weekly_fpts, reverse=True)
    top = sorted_adds[:top_n]
    avg = sum(a.weekly_fpts for a in top) / len(top)
    return avg * discount


def _should_defer(
    candidate: TransactionCandidate,
    adds_remaining: int,
    remaining_game_days: int,
    expected_future_value: float,
    deferral_threshold: float = 1.1,
) -> bool:
    """Should we save this add slot for a potentially better future use?

    Only defer if:
    1. We have more remaining adds than remaining high-value game days
    2. The expected future value exceeds this candidate's value by the
       deferral threshold (default 10% buffer)

    If adds_remaining <= remaining_game_days, use it or lose it.
    """
    if adds_remaining <= remaining_game_days:
        return False  # can't afford to waste add slots

    if candidate.adjusted_score <= 0:
        return True  # negative value — always defer

    return expected_future_value > candidate.adjusted_score * deferral_threshold


_OPPORTUNITY_COST_PERCENTILE: dict[AggressionLevel, float] = {
    # Higher percentile = stricter bar = fewer adds.
    # The threshold is taken over positive-score candidates in the pool, so
    # "everyone on my team is better than the FAs" -> thin pool -> high bar
    # naturally rejects mediocre swaps.
    AggressionLevel.CONSERVATIVE: 0.80,
    AggressionLevel.NORMAL: 0.50,
    AggressionLevel.AGGRESSIVE: 0.20,
    AggressionLevel.DESPERATE: 0.0,
}


def _compute_opportunity_cost_threshold(
    add_targets: list[PlayerValue],
    drop_candidates: list[PlayerValue],
    replacement: ReplacementLevel,
    aggression: AggressionLevel,
) -> float:
    """Derive a min-score threshold from the score distribution of the pool.

    The opportunity cost of burning an add is the alternative swap you
    *could* have made instead. We approximate it by scoring every (add, drop)
    pair, taking the positive-score ones, and picking a percentile based on
    aggression. CONSERVATIVE demands top-quintile swaps; DESPERATE accepts
    anything net-positive.
    """
    scores: list[float] = []
    for add in add_targets:
        for drop in drop_candidates:
            s, _ = score_transaction(add, drop, replacement, aggression)
            if s > 0:
                scores.append(s)

    if not scores:
        return 0.0

    pct = _OPPORTUNITY_COST_PERCENTILE[aggression]
    if pct <= 0:
        return 0.0

    scores.sort()
    idx = int(pct * (len(scores) - 1))
    return scores[idx]


def _count_remaining_game_days(
    add_targets: list[PlayerValue],
    from_date: date | None = None,
) -> int:
    """Count unique game days remaining in the week across all add targets."""
    if from_date is None:
        from_date = date.today()

    all_dates: set[date] = set()
    for pv in add_targets:
        for gd in pv.game_projections:
            if gd >= from_date:
                all_dates.add(gd)

    return len(all_dates)


def optimize_week(
    roster: Roster,
    add_targets: list[PlayerValue],
    drop_candidates: list[PlayerValue],
    yahoo_week: int,
    replacement: ReplacementLevel,
    adds_remaining: int = 4,
    aggression: AggressionLevel = AggressionLevel.NORMAL,
    sim_date: date | None = None,
) -> WeekPlan:
    """Find the optimal set of add/drop transactions for a week.

    Uses greedy forward search with look-ahead:
    1. Score every (add, drop) pair
    2. Pick the best candidate
    3. Check if deferring would yield a better result later
    4. If not deferring, select it and re-score remaining pairs
    5. Repeat up to adds_remaining times

    The key insight: each transaction changes the roster, which changes
    the value of subsequent transactions. A Tuesday add might fill a
    slot that makes a Wednesday add less valuable. The greedy approach
    handles this by re-scoring after each selection.

    Args:
        roster: Current fantasy roster
        add_targets: Free agents to consider (with PlayerValue computed)
        drop_candidates: Roster players eligible to drop (from rank_drops)
        yahoo_week: Fantasy week number
        replacement: FA baseline
        adds_remaining: Number of adds available (typically 4)
        aggression: How aggressively to stream
        sim_date: Simulated "today" for backtesting (defaults to actual today)

    Returns:
        WeekPlan with up to `adds_remaining` transactions.
    """
    if sim_date is None:
        sim_date = date.today()

    # Opportunity cost: derive a min-score from the *initial* pool so the
    # bar reflects "what does this week's full FA market actually offer".
    # Computing per-round is self-defeating because the best swap of any
    # pool always clears that pool's own percentile.
    min_score = _compute_opportunity_cost_threshold(
        add_targets, drop_candidates, replacement, aggression
    )

    selected: list[TransactionCandidate] = []
    used_add_ids: set[int] = set()
    used_drop_ids: set[int] = set()
    deferred_count = 0

    for round_num in range(adds_remaining):
        # Filter to unused adds and drops
        available_adds = [a for a in add_targets if a.nhl_id not in used_add_ids]
        available_drops = [d for d in drop_candidates if d.nhl_id not in used_drop_ids]

        if not available_adds or not available_drops:
            break

        candidates = build_candidates(
            available_adds, available_drops, replacement, aggression,
            min_score=min_score,
        )

        if not candidates:
            break

        best = candidates[0]

        # Look-ahead: should we defer this add?
        slots_left = adds_remaining - len(selected)
        remaining_days = _count_remaining_game_days(available_adds, from_date=sim_date)
        future_value = _compute_expected_future_value(available_adds)

        if slots_left > 1 and _should_defer(
            best, slots_left, remaining_days, future_value
        ):
            deferred_count += 1
            # Skip this round but don't consume the slot
            # Mark this add as "considered but deferred" to avoid loops
            if deferred_count >= adds_remaining:
                break  # safety: don't loop forever
            continue

        selected.append(best)
        used_add_ids.add(best.add_player.nhl_id)
        if best.drop_player:
            used_drop_ids.add(best.drop_player.nhl_id)

    total_gain = sum(t.net_weekly_fpts for t in selected)

    reasoning_parts = [
        f"{len(selected)} transactions, {total_gain:+.1f} projected weekly FPTS"
    ]
    if deferred_count > 0:
        reasoning_parts.append(f"{deferred_count} deferred (saving slots for better options)")

    return WeekPlan(
        yahoo_week=yahoo_week,
        transactions=selected,
        adds_used=len(selected),
        projected_fpts_gain=total_gain,
        aggression=aggression,
        reasoning="; ".join(reasoning_parts),
    )

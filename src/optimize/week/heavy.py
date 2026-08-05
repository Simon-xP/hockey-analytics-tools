"""Heavy week optimization — the full pipeline, used for my own team.

Scores individual add/drop transactions at the pickup-player level and
allocates up to 4 adds across a week to maximize total FPTS gained.

Unlike `light`, this path is aggression-aware, ranks drop candidates, and
searches concrete (add, drop) pairs rather than assuming the best available
free agents get picked up.

The scoring formula balances short-term (weekly) and long-term (ROS)
value, shifted by aggression level based on matchup context.

The optimizer uses greedy forward search with look-ahead: pick the
best transaction, apply it to the roster, re-score remaining candidates
against updated state, and optionally defer if a better option might
appear later in the week.
"""

from datetime import date

from sqlalchemy.orm import Session

from src.optimize.models import (
    AGGRESSION_WEIGHTS,
    AggressionLevel,
    PlayerValue,
    ReplacementLevel,
    Roster,
    RosterSlotSettings,
    TeamWeekResult,
    TransactionCandidate,
    WeekPlan,
)

_POOL_RELATIVE_TOP_PCT = 0.20
_POOL_RELATIVE_BOTTOM_PCT = 0.20
_POOL_RELATIVE_MAGNITUDE = 1.0


def _apply_pool_relative_scaling(pool: list[PlayerValue]) -> None:
    """Rescale raw upside and opportunity scores against the FA pool distribution.

    Raw scores are small (~[-0.3, 0.3]) and most players cluster near zero.
    Pool-relative scaling sharpens the signal:

    - Top 20% → +1.0
    - Bottom 20% → -1.0
    - Middle 60% → 0.0

    Mutates both `upside_score` and `opportunity_score` in place.
    """
    if len(pool) < 5:
        return

    for attr in ("upside_score", "opportunity_score"):
        scored = [(p, getattr(p, attr)) for p in pool]
        scored.sort(key=lambda t: t[1], reverse=True)
        n = len(scored)
        top_cutoff = max(1, int(n * _POOL_RELATIVE_TOP_PCT))
        bottom_cutoff = max(1, int(n * _POOL_RELATIVE_BOTTOM_PCT))

        for i, (player, _) in enumerate(scored):
            if i < top_cutoff:
                setattr(player, attr, _POOL_RELATIVE_MAGNITUDE)
            elif i >= n - bottom_cutoff:
                setattr(player, attr, -_POOL_RELATIVE_MAGNITUDE)
            else:
                setattr(player, attr, 0.0)


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

    # Upside bonus: picking up high-upside (talent) players is valuable
    # Protects high-upside players from being dropped
    upside_bonus = add.upside_score * 1.5 - max(0, drop.upside_score) * 1.5
    if abs(upside_bonus) > 0.1:
        reasoning.append(f"Upside adjustment: {upside_bonus:+.1f}")

    # Opportunity bonus: player in a favorable situation right now
    opportunity_bonus = add.opportunity_score * 1.0 - max(0, drop.opportunity_score) * 1.0
    if abs(opportunity_bonus) > 0.1:
        reasoning.append(f"Opportunity adjustment: {opportunity_bonus:+.1f}")

    # Replacement level context
    repl = replacement.for_positions(drop.positions)
    if drop.fpts_per_game < repl:
        reasoning.append(f"Drop is below replacement ({drop.fpts_per_game:.1f} < {repl:.1f} FPTS/GP)")

    score = (
        w_quality * quality_component
        + w_schedule * schedule_component
        - scarcity_penalty
        + upside_bonus
        + opportunity_bonus
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


def plan_week(
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

    Pure allocator: takes already-valued candidates and returns a plan.
    `optimize_week_heavy()` is the session-level entry point that builds
    those candidates from the database.

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

    _apply_pool_relative_scaling(add_targets)

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


def optimize_week_heavy(
    session: Session,
    league_key: str,
    team_key: str,
    as_of: date,
    week_end: date,
    yahoo_week: int,
    earned: float = 0.0,
    adds_remaining: int = 4,
    aggression: AggressionLevel = AggressionLevel.NORMAL,
    season: str = "20252026",
    roster_slot_settings: RosterSlotSettings | None = None,
    forecast_fn=None,
    protected_nhl_ids: set[int] | None = None,
    fa_candidate_limit: int = 60,
) -> TeamWeekResult:
    """Full heavy pass for one team: value every candidate, then plan the week.

    Builds the roster and FA pool from the database, computes a slot-aware
    `PlayerValue` for each side, ranks drop candidates, and runs the greedy
    allocator. Returns a `TeamWeekResult` whose `plan` holds the concrete
    transactions to execute.
    """
    # Imported here to keep module import cheap and avoid a cycle with
    # value.py, which imports scoring helpers from this module's siblings.
    from src.optimize.drops import get_drop_candidates
    from src.optimize.replacement import compute_replacement_level
    from src.optimize.value import compute_player_value
    from src.optimize.week.light import (
        _build_roster_players,
        get_free_agent_nhl_ids,
        get_team_roster_nhl_ids,
        model_pickup_boost,
        project_team_remaining,
    )

    if roster_slot_settings is None:
        roster_slot_settings = RosterSlotSettings()

    roster_nhl_ids = get_team_roster_nhl_ids(session, league_key, team_key)
    roster = Roster(
        players=_build_roster_players(session, roster_nhl_ids),
        roster_slot_settings=roster_slot_settings,
    )

    projection = project_team_remaining(
        session, league_key, team_key,
        as_of=as_of, week_end=week_end, earned=earned,
        roster_slot_settings=roster_slot_settings,
    )

    fa_nhl_ids = get_free_agent_nhl_ids(session, league_key)[:fa_candidate_limit]

    add_targets: list[PlayerValue] = []
    for fa_id in fa_nhl_ids:
        pv = compute_player_value(
            session, fa_id, roster, yahoo_week, season, forecast_fn
        )
        if pv is not None and pv.weekly_fpts > 0:
            add_targets.append(pv)

    replacement = compute_replacement_level(
        session,
        free_agents=[
            {"name": pv.name, "team": pv.team, "position": ",".join(pv.positions)}
            for pv in add_targets
        ],
        season=season,
        as_of=as_of,
    )

    drop_candidates = get_drop_candidates(
        session, roster, yahoo_week, replacement,
        max_candidates=8, season=season,
        protected_nhl_ids=protected_nhl_ids,
    )

    if add_targets and drop_candidates:
        plan = plan_week(
            roster=roster,
            add_targets=add_targets,
            drop_candidates=drop_candidates,
            yahoo_week=yahoo_week,
            replacement=replacement,
            adds_remaining=adds_remaining,
            aggression=aggression,
            sim_date=as_of,
        )
    else:
        plan = WeekPlan(
            yahoo_week=yahoo_week,
            transactions=[],
            adds_used=0,
            projected_fpts_gain=0.0,
            aggression=aggression,
            reasoning="No viable transactions found",
        )

    pickup_boost = model_pickup_boost(
        session, league_key, team_key,
        adds_remaining=adds_remaining,
        as_of=as_of, week_end=week_end,
        roster_slot_settings=roster_slot_settings,
    )

    return TeamWeekResult(
        team_key=team_key,
        depth="heavy",
        projection=projection,
        pickup_boost=pickup_boost,
        plan=plan,
    )

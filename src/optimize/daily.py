"""PuckAgent transaction strategy — the core decision engine.

Implements the 5-stage decision flow from docs/transaction-strategy.md:
1. Free adds (empty roster slots)
2. Drop evaluation (find cheapest drop)
3. FA ranking (find best add)
4. Fire or defer (compare net value against threshold)
5. Repeat (loop if adds remaining)

All tunable parameters are in StrategyConfig.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from src.optimize.roster_state import RosterPlayerState
from src.optimize.models import AggressionLevel


@dataclass
class FACandidate:
    nhl_id: int
    name: str
    positions: list[str]
    team_id: int
    team_abbrev: str
    fpts_per_gp: float
    remaining_games_this_week: int
    upside_score: float = 0.0


@dataclass
class Transaction:
    add: FACandidate
    drop: RosterPlayerState | None
    add_value: float
    drop_score: float | None
    net_value: float
    reasoning: str


FIRE_THRESHOLDS: dict[AggressionLevel, float] = {
    AggressionLevel.CONSERVATIVE: 2.0,
    AggressionLevel.NORMAL: 0.5,
    AggressionLevel.AGGRESSIVE: 0.1,
    AggressionLevel.DESPERATE: 0.1,
}

INJURY_PENALTY_MULT: dict[AggressionLevel, float] = {
    AggressionLevel.CONSERVATIVE: 0.0,
    AggressionLevel.NORMAL: 0.3,
    AggressionLevel.AGGRESSIVE: 0.6,
    AggressionLevel.DESPERATE: 1.0,
}


@dataclass
class StrategyConfig:
    upside_weight: float = 0.15
    must_fire_floor: float = 0.1
    min_remaining_games: int = 1
    max_roster_size: int = 15


def decide(
    roster: list[RosterPlayerState],
    fa_pool: list[FACandidate],
    adds_remaining: int,
    days_remaining_in_week: int,
    aggression: AggressionLevel,
    config: StrategyConfig | None = None,
) -> list[Transaction]:
    """Run the 5-stage transaction decision.

    Returns a list of transactions to execute (may be empty if deferring).
    """
    if config is None:
        config = StrategyConfig()

    if adds_remaining <= 0 or not fa_pool:
        return []

    # Work on mutable copies
    active_roster = list(roster)
    available_fas = list(fa_pool)
    budget = adds_remaining
    transactions: list[Transaction] = []

    threshold = FIRE_THRESHOLDS[aggression]
    injury_mult = INJURY_PENALTY_MULT[aggression]

    while budget > 0 and available_fas:
        # --- Stage 1: Check for empty slots ---
        ir_players = [
            p for p in active_roster
            if p.injury_status in ("IR", "IR+")
        ]
        active_count = len(active_roster) - len(ir_players)
        has_empty_slot = active_count < config.max_roster_size

        # --- Stage 2: Drop evaluation (skip if free slot) ---
        drop_candidate = None
        drop_score = None
        if not has_empty_slot:
            scored_drops = _score_drops(active_roster, aggression, config)
            if not scored_drops:
                break
            drop_candidate, drop_score = scored_drops[0]

        # --- Stage 3: FA ranking ---
        ranked_fas = _rank_fas(available_fas, config)
        if not ranked_fas:
            break
        best_fa, add_value = ranked_fas[0]

        # --- Stage 4: Fire or defer ---
        if has_empty_slot:
            net = add_value
            reasoning = (
                f"Free add (empty slot): {best_fa.name} "
                f"({best_fa.fpts_per_gp:.2f}/GP × {best_fa.remaining_games_this_week}GP "
                f"= {add_value:.1f} add_value)"
            )
        else:
            net = add_value - drop_score
            reasoning = (
                f"ADD {best_fa.name} ({best_fa.fpts_per_gp:.2f}/GP, "
                f"{best_fa.remaining_games_this_week}GP, add_value={add_value:.1f}) "
                f"DROP {drop_candidate.name} (drop_score={drop_score:.1f}) "
                f"net={net:+.1f}"
            )

        # Determine effective threshold
        must_fire = budget >= days_remaining_in_week
        effective_threshold = config.must_fire_floor if must_fire else threshold

        if net < effective_threshold:
            if must_fire:
                reasoning += f" — below must-fire floor ({config.must_fire_floor}), skipping"
            break

        if must_fire and net < threshold:
            reasoning += f" — must-fire (budget={budget} >= days_left={days_remaining_in_week})"

        txn = Transaction(
            add=best_fa,
            drop=drop_candidate,
            add_value=round(add_value, 2),
            drop_score=round(drop_score, 2) if drop_score is not None else None,
            net_value=round(net, 2),
            reasoning=reasoning,
        )
        transactions.append(txn)

        # --- Stage 5: Update state and repeat ---
        available_fas = [f for f in available_fas if f.nhl_id != best_fa.nhl_id]
        if drop_candidate:
            active_roster = [p for p in active_roster if p.nhl_id != drop_candidate.nhl_id]
        budget -= 1

    return transactions


def _score_drops(
    roster: list[RosterPlayerState],
    aggression: AggressionLevel,
    config: StrategyConfig,
) -> list[tuple[RosterPlayerState, float]]:
    """Score each roster player for droppability. Lower = more droppable."""
    injury_mult = INJURY_PENALTY_MULT[aggression]
    scored = []

    for player in roster:
        # Skip IR players — they're not occupying an active slot
        if player.injury_status in ("IR", "IR+"):
            continue

        # Skip goalies — separate evaluation path (future work)
        if "G" in player.positions and len(player.positions) == 1:
            continue

        upside_bonus = player.upside_score * config.upside_weight * player.fpts_per_gp
        effective_value = player.remaining_weekly_fpts + upside_bonus

        injury_penalty = (
            player.estimated_games_missed * player.fpts_per_gp * injury_mult
        )

        drop_score = effective_value - injury_penalty
        scored.append((player, drop_score))

    scored.sort(key=lambda x: x[1])
    return scored


def _rank_fas(
    fa_pool: list[FACandidate],
    config: StrategyConfig,
) -> list[tuple[FACandidate, float]]:
    """Rank FA candidates by add_value. Higher = better pickup."""
    scored = []

    for fa in fa_pool:
        if fa.remaining_games_this_week < config.min_remaining_games:
            continue

        upside_bonus = fa.upside_score * config.upside_weight * fa.fpts_per_gp
        add_value = fa.fpts_per_gp * fa.remaining_games_this_week + upside_bonus
        scored.append((fa, add_value))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored

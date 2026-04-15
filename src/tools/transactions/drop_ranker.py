"""Drop ranking — identify which roster players are most droppable.

Ranks roster players from most droppable to least using a composite
score of weekly value, position scarcity, and upside.

The most droppable player is the one whose removal costs the least
in weekly FPTS and whose position is easiest to cover.
"""

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from src.tools.schedule.models import Roster, RosterPlayer
from src.tools.transactions.models import PlayerValue, ReplacementLevel
from src.tools.transactions.player_value import compute_player_value_simple


def compute_position_scarcity(
    player: RosterPlayer,
    roster: Roster,
) -> float:
    """How costly is it to lose this player's position coverage?

    Returns 0.0 to 1.0:
        1.0 = sole provider at a position (dropping leaves a slot unfillable)
        0.0 = many alternatives exist

    Logic:
    - For each position the player is eligible for, count how many
      OTHER roster players are also eligible
    - Compare against the number of slots needed at that position
    - Take the maximum scarcity across all positions (worst case)
    - Multi-position eligible players get a 15% discount (more flexible
      to replace)
    """
    active_slots = roster.roster_slot_settings.active_slots()
    max_scarcity = 0.0

    skater_positions = [p for p in player.positions if p != "G"]

    for pos in player.positions:
        slots_needed = active_slots.get(pos, 0)
        if slots_needed == 0:
            continue

        # Count other roster players eligible at this position
        others_at_pos = sum(
            1
            for p in roster.players
            if pos in p.positions and p.nhl_id != player.nhl_id
        )

        if others_at_pos == 0:
            scarcity = 1.0  # sole provider — very costly to drop
        elif others_at_pos < slots_needed:
            # Not enough coverage without this player
            scarcity = (slots_needed - others_at_pos) / slots_needed
        else:
            # Enough coverage — scarcity based on how tight it is
            surplus = others_at_pos - slots_needed
            scarcity = max(0.0, 0.3 - surplus * 0.1)  # slight penalty even with coverage

        max_scarcity = max(max_scarcity, scarcity)

    # Multi-position discount: C/LW is easier to replace than pure C
    if len(skater_positions) > 1:
        max_scarcity *= 0.85

    return round(max_scarcity, 3)


def rank_drops(
    session: Session,
    roster: Roster,
    yahoo_week: int,
    replacement_level: ReplacementLevel,
    season: str = "20252026",
    protected_nhl_ids: Optional[set[int]] = None,
    as_of: Optional[date] = None,
) -> list[PlayerValue]:
    """Rank roster players from most droppable to least.

    Uses simple (non-slot-aware) valuation because the question is
    "what do we lose by dropping this player?" — the answer includes
    ALL their games, since dropping them frees a slot for replacements.

    Args:
        session: DB session
        roster: Current fantasy roster
        yahoo_week: Week to evaluate
        replacement_level: FA baseline for value context
        season: Season string
        protected_nhl_ids: Players that cannot be dropped (user-configured)
        as_of: Knowledge-cutoff date (for backtesting). Historical stats are
            capped strictly before this date, and ROS is counted from it.

    Returns:
        List of PlayerValue sorted from most droppable (index 0) to least.
        Each PlayerValue has position_scarcity populated.
    """
    if protected_nhl_ids is None:
        protected_nhl_ids = set()

    player_values: list[PlayerValue] = []

    for roster_player in roster.players:
        if roster_player.nhl_id is None:
            continue

        if roster_player.nhl_id in protected_nhl_ids:
            continue

        # Use simple valuation for rostered players. The question is
        # "what is this player worth?" not "can they fit in a slot today?"
        # A roster-crunched player (like M.Tkachuk when LW/RW are full)
        # still has value — dropping them frees a slot for someone else.
        pv = compute_player_value_simple(
            session=session,
            nhl_id=roster_player.nhl_id,
            yahoo_week=yahoo_week,
            season=season,
            as_of=as_of,
        )
        if pv is None:
            continue

        # Attach position scarcity
        pv.position_scarcity = compute_position_scarcity(roster_player, roster)

        player_values.append(pv)

    # Sort by droppability: most droppable first
    # Droppability = weekly value + scarcity bonus + upside bonus
    # Lower score = more droppable
    player_values.sort(key=lambda pv: _droppability_score(pv, replacement_level))

    return player_values


def _droppability_score(pv: PlayerValue, replacement: ReplacementLevel) -> float:
    """Compute a score where lower = more droppable.

    Components:
    1. weekly_fpts: raw contribution this week
    2. value_over_replacement: how much better than a free agent
    3. position_scarcity: penalty for dropping scarce positions
    4. upside_score: hold bonus for upside players (populated in Milestone 6)
    """
    # Value over replacement (per game)
    repl_level = replacement.for_positions(pv.positions)
    value_over_repl = pv.fpts_per_game - repl_level

    # Scarcity bonus: scarce position players are harder to drop
    # Scaled so a sole-provider (1.0) adds ~2 FPTS equivalent of protection
    scarcity_bonus = pv.position_scarcity * 2.0

    # Upside bonus: high upside players get protection
    # Scaled so max upside (1.0) adds ~1.5 FPTS equivalent of protection
    upside_bonus = max(0, pv.upside_score) * 1.5

    return pv.weekly_fpts + value_over_repl + scarcity_bonus + upside_bonus


def get_drop_candidates(
    session: Session,
    roster: Roster,
    yahoo_week: int,
    replacement_level: ReplacementLevel,
    max_candidates: int = 5,
    season: str = "20252026",
    protected_nhl_ids: Optional[set[int]] = None,
    as_of: Optional[date] = None,
) -> list[PlayerValue]:
    """Get the top N most droppable players.

    Convenience wrapper around rank_drops() that returns only the
    most droppable candidates.
    """
    ranked = rank_drops(
        session=session,
        roster=roster,
        yahoo_week=yahoo_week,
        replacement_level=replacement_level,
        season=season,
        protected_nhl_ids=protected_nhl_ids,
        as_of=as_of,
    )
    return ranked[:max_candidates]

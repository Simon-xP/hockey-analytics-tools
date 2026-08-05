"""Drop ranking -- identify which roster players are most droppable.

Ranks roster players from most droppable to least using a composite
score of weekly value, position scarcity, and upside.

The most droppable player is the one whose removal costs the least
in weekly FPTS and whose position is easiest to cover.
"""

import logging
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from src.core.models import Player, PlayerValuation, Team
from src.optimize.models import Roster, RosterPlayer
from src.optimize.models import PlayerType, PlayerValue, ReplacementLevel
from src.optimize.value import get_team_remaining_games, get_team_week_games

log = logging.getLogger(__name__)


def _valuation_to_player_value(
    session: Session,
    valuation: PlayerValuation,
    yahoo_week: int,
) -> Optional[PlayerValue]:
    """Build a PlayerValue from a stored PlayerValuation."""
    player = session.query(Player).filter(Player.nhl_id == valuation.nhl_id).first()
    if not player:
        return None

    team = session.query(Team).filter(Team.team_id == player.team_id).first()
    if not team:
        return None

    if player.yahoo_positions:
        positions = [p.strip() for p in player.yahoo_positions.split(",")]
    else:
        nhl_to_yahoo = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}
        positions = [nhl_to_yahoo.get(player.position, player.position)]

    week_forecasts = valuation.forecasts_for_week(yahoo_week, session)
    games_in_window = len(week_forecasts)
    window_fpts = sum(f["fpts"] for f in week_forecasts)
    fpts_per_game = window_fpts / games_in_window if games_in_window > 0 else valuation.fpts_per_game

    week_games = get_team_week_games(session, team.abbrev, yahoo_week)
    window_start = min(g.date for g in week_games) if week_games else None
    window_end = max(g.date for g in week_games) if week_games else None
    window_days = (window_end - window_start).days + 1 if window_start and window_end else 7

    remaining_games = get_team_remaining_games(session, team.abbrev)

    return PlayerValue(
        nhl_id=valuation.nhl_id,
        name=player.full_name,
        team=team.abbrev,
        positions=positions,
        player_type=PlayerType.SKATER,
        fpts_per_game=fpts_per_game,
        games_in_window=games_in_window,
        fillable_games=games_in_window,
        window_fpts=window_fpts,
        window_start=window_start,
        window_end=window_end,
        window_days=window_days,
        avg_toi=valuation.avg_toi or 0.0,
        games_played=valuation.games_played or 0,
        ros_value=fpts_per_game * remaining_games,
        game_projections={
            date.fromisoformat(f["date"]): f["fpts"] for f in week_forecasts
        },
        upside_score=valuation.upside_score or 0.0,
        opportunity_score=valuation.opportunity_score or 0.0,
    )


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

        others_at_pos = sum(
            1
            for p in roster.players
            if pos in p.positions and p.nhl_id != player.nhl_id
        )

        if others_at_pos == 0:
            scarcity = 1.0
        elif others_at_pos < slots_needed:
            scarcity = (slots_needed - others_at_pos) / slots_needed
        else:
            surplus = others_at_pos - slots_needed
            scarcity = max(0.0, 0.3 - surplus * 0.1)

        max_scarcity = max(max_scarcity, scarcity)

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

    Reads pre-computed valuations from the player_valuations table.
    Falls back to compute_player_value_simple for players that haven't
    been synced yet.

    Non-slot-aware because the question is "what do we lose by dropping
    this player?" -- the answer includes ALL their games, since dropping
    them frees a slot for replacements.
    """
    if protected_nhl_ids is None:
        protected_nhl_ids = set()

    player_values: list[PlayerValue] = []

    for roster_player in roster.players:
        if roster_player.nhl_id is None:
            continue

        if roster_player.nhl_id in protected_nhl_ids:
            continue

        valuation = session.get(PlayerValuation, roster_player.nhl_id)

        if valuation:
            pv = _valuation_to_player_value(session, valuation, yahoo_week)
        else:
            from src.optimize.value import compute_player_value_simple
            log.warning(
                "No stored valuation for %s (%d), computing on-demand",
                roster_player.name, roster_player.nhl_id,
            )
            pv = compute_player_value_simple(
                session=session,
                nhl_id=roster_player.nhl_id,
                yahoo_week=yahoo_week,
                season=season,
                as_of=as_of,
            )

        if pv is None:
            continue

        pv.position_scarcity = compute_position_scarcity(roster_player, roster)
        player_values.append(pv)

    player_values.sort(key=lambda pv: _droppability_score(pv, replacement_level))
    return player_values


def _droppability_score(pv: PlayerValue, replacement: ReplacementLevel) -> float:
    """Compute a score where lower = more droppable.

    Components:
    1. weekly_fpts: raw contribution this week
    2. value_over_replacement: how much better than a free agent
    3. position_scarcity: penalty for dropping scarce positions
    4. upside_score: hold bonus for upside players
    """
    repl_level = replacement.for_positions(pv.positions)
    value_over_repl = pv.fpts_per_game - repl_level
    scarcity_bonus = pv.position_scarcity * 2.0
    upside_bonus = max(0, pv.upside_score) * 1.5
    opportunity_bonus = max(0, pv.opportunity_score) * 1.0

    return pv.weekly_fpts + value_over_repl + scarcity_bonus + upside_bonus + opportunity_bonus


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
    """Get the top N most droppable players."""
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

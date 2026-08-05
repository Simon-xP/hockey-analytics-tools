"""Replacement level computation from the free agent pool.

Replacement level = the average FPTS/game of the top N free agents at
each position group. This represents what you can get "for free" at
any time — the baseline against which all roster decisions are measured.

No goalie replacement level: goalie value depends on volume (starts),
not rate (FPTS/start), so streaming goalies are evaluated differently.
"""

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from src.core.resolver import resolve_player
from src.core.queries.stats_helpers import compute_fpts_per_gp
from src.optimize.models import ReplacementLevel


def compute_replacement_level(
    session: Session,
    free_agents: list[dict],
    season: str = "20252026",
    top_n: int = 5,
    min_gp: int = 10,
    as_of: Optional[date] = None,
) -> ReplacementLevel:
    """Compute replacement level FPTS/game from the free agent pool.

    Args:
        session: DB session
        free_agents: List of dicts from Yahoo API (get_free_agents()).
            Each dict has: name, team, position, player_id, etc.
        season: Season string for stats lookup
        top_n: Number of top free agents to average for replacement level
        min_gp: Minimum games played to be considered

    Returns:
        ReplacementLevel with forward and defense baselines.
    """
    forward_fpts = []
    defense_fpts = []

    for fa in free_agents:
        position = fa.get("position", "")
        if not position:
            continue

        # Skip goalies — no goalie replacement level
        if "G" in position and "LW" not in position and "RW" not in position:
            continue

        # Resolve to NHL ID
        nhl_id = None
        name = fa.get("name")
        team = fa.get("team")

        if name:
            try:
                nhl_id = resolve_player(session, name=name, team_abbrev=team)
            except Exception:
                continue

        if not nhl_id:
            continue

        # Get historical FPTS/GP (capped at as_of to prevent leakage)
        fpts_data = compute_fpts_per_gp(session, nhl_id, season, as_of=as_of)
        if not fpts_data or fpts_data["gp"] < min_gp:
            continue

        fpts_per_gp = fpts_data["fpts_per_gp"]

        # Categorize by position
        if "D" in position:
            defense_fpts.append(fpts_per_gp)
        else:
            forward_fpts.append(fpts_per_gp)

    # Sort descending and take top N
    forward_fpts.sort(reverse=True)
    defense_fpts.sort(reverse=True)

    fwd_top = forward_fpts[:top_n]
    def_top = defense_fpts[:top_n]

    return ReplacementLevel(
        forward=sum(fwd_top) / len(fwd_top) if fwd_top else 0.0,
        defense=sum(def_top) / len(def_top) if def_top else 0.0,
        computed_at=as_of or date.today(),
        sample_sizes={
            "forward": len(forward_fpts),
            "defense": len(defense_fpts),
        },
    )

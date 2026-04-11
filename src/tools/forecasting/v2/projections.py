"""Projection combiner: per-60 rates × predicted TOI → per-game counts.

This is the final step of the prediction pipeline. Combines situation-specific
per-60 rate predictions with TOI predictions to produce per-game stat
projections and fantasy point estimates.
"""

from src.tools.fantasy.scoring import SKATER_WEIGHTS

# PP/SH bonus weights (on top of base goal/assist values)
PP_GOAL_BONUS = 1.3
PP_ASSIST_BONUS = 1.0
SH_GOAL_BONUS = 3.0
SH_ASSIST_BONUS = 2.0


def project_per_game(
    situation_rates: dict[str, dict[str, float]],
    situation_toi: dict[str, float],
) -> dict[str, float]:
    """Combine per-60 rates and TOI predictions into per-game projections.

    Args:
        situation_rates: Dict of situation -> {stat_per60: value}.
            e.g. {"5v5": {"goals_per60": 1.2, ...}, "pp": {"goals_per60": 3.5, ...}}
        situation_toi: Dict of situation -> predicted TOI in seconds.
            e.g. {"5v5": 960, "pp": 180, "pk": 120, "other": 30}

    Returns:
        Dict with:
            - Per-game stat projections: "goals", "assists", "shots", "hits", "blocks"
            - Situation-split projections: "5v5_goals", "pp_goals", etc.
            - Fantasy points: "fpts"
    """
    result = {}

    # Stats to aggregate across situations
    all_stats = ["goals", "assists", "shots", "hits", "blocks"]

    # Initialize totals
    for stat in all_stats:
        result[stat] = 0.0

    # Compute per-game count for each situation
    for situation, rates in situation_rates.items():
        toi_seconds = situation_toi.get(situation, 0.0)
        toi_minutes = toi_seconds / 60.0

        for stat in all_stats:
            rate = rates.get(f"{stat}_per60", 0.0)
            per_game = rate * toi_minutes / 60.0  # per-60 rate × (toi_min / 60)
            result[f"{situation}_{stat}"] = per_game
            result[stat] += per_game

    # Compute fantasy points with situation bonuses
    fpts = 0.0

    # Base stat scoring
    for stat in all_stats:
        if stat in SKATER_WEIGHTS:
            fpts += result[stat] * SKATER_WEIGHTS[stat]

    # PP bonuses (on top of base scoring)
    pp_goals = result.get("pp_goals", 0.0)
    pp_assists = result.get("pp_assists", 0.0)
    fpts += pp_goals * PP_GOAL_BONUS
    fpts += pp_assists * PP_ASSIST_BONUS

    # SH bonuses
    pk_goals = result.get("pk_goals", 0.0)
    pk_assists = result.get("pk_assists", 0.0)
    fpts += pk_goals * SH_GOAL_BONUS
    fpts += pk_assists * SH_ASSIST_BONUS

    result["fpts"] = fpts

    # Also store total TOI for reference
    result["total_toi"] = sum(situation_toi.values())

    return result


def compute_actual_fpts(
    game_stats: dict[str, dict],
    situation_toi: dict[str, float],
) -> float:
    """Compute actual fantasy points from real game stats.

    Args:
        game_stats: Dict of situation -> {stat: raw_count}.
        situation_toi: Dict of situation -> actual TOI in seconds.

    Returns:
        Actual fantasy points for the game.
    """
    fpts = 0.0

    total_goals = 0
    total_assists = 0
    total_shots = 0
    total_hits = 0
    total_blocks = 0

    pp_goals = 0
    pp_assists = 0
    pk_goals = 0
    pk_assists = 0

    for situation, stats in game_stats.items():
        goals = stats.get("goals", 0)
        assists = stats.get("assists", 0)
        shots = stats.get("shots", 0)
        hits = stats.get("hits", 0)
        blocks = stats.get("blocks", 0)

        total_goals += goals
        total_assists += assists
        total_shots += shots
        total_hits += hits
        total_blocks += blocks

        if situation == "pp":
            pp_goals += goals
            pp_assists += assists
        elif situation == "pk":
            pk_goals += goals
            pk_assists += assists

    # Base scoring
    fpts += total_goals * SKATER_WEIGHTS.get("goals", 0)
    fpts += total_assists * SKATER_WEIGHTS.get("assists", 0)
    fpts += total_shots * SKATER_WEIGHTS.get("shots", 0)
    fpts += total_hits * SKATER_WEIGHTS.get("hits", 0)
    fpts += total_blocks * SKATER_WEIGHTS.get("blocks", 0)

    # Situation bonuses
    fpts += pp_goals * PP_GOAL_BONUS
    fpts += pp_assists * PP_ASSIST_BONUS
    fpts += pk_goals * SH_GOAL_BONUS
    fpts += pk_assists * SH_ASSIST_BONUS

    return fpts

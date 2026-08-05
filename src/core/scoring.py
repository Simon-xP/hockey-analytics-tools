"""Fantasy scoring weights and point projection.

Converts per-60 stat predictions into fantasy points per game.
Scoring weights are configurable per league.
"""

# Default scoring weights (from user's Yahoo league)
SKATER_WEIGHTS = {
    "goals": 3.0,
    "assists": 2.0,
    "pim": 0.3,
    # PP/SH bonuses are on top of base goal/assist values.
    # We don't separate situations yet, so these aren't applied.
    # "pp_goals_bonus": 1.3,
    # "pp_assists_bonus": 1.0,
    # "sh_goals_bonus": 3.0,
    # "sh_assists_bonus": 2.0,
    "shots": 0.3,
    "hits": 0.4,
    "blocks": 0.5,
}

GOALIE_WEIGHTS = {
    "wins": 3.3,
    "goals_against": -1.25,
    "saves": 0.28,
    "shutouts": 2.3,
}

# Mapping from forecast stat (per-60) to fantasy scoring category
FORECAST_TO_FANTASY = {
    "goals_per_60": "goals",
    "assists_per_60": "assists",
    "shots_per_60": "shots",
    "hits_per_60": "hits",
    "blocked_per_60": "blocks",
}


def projected_fpts_per_game(per_60_predictions: dict, avg_toi: float) -> float:
    """Convert per-60 rate predictions to fantasy points per game.

    Args:
        per_60_predictions: dict of stat -> per-60 rate
            e.g. {"goals_per_60": 1.5, "assists_per_60": 2.0, ...}
        avg_toi: average minutes per game for the player

    Returns:
        Projected fantasy points for one game.
    """
    if avg_toi <= 0:
        return 0.0

    toi_fraction = avg_toi / 60.0
    total = 0.0

    for per_60_stat, fantasy_cat in FORECAST_TO_FANTASY.items():
        rate = per_60_predictions.get(per_60_stat)
        if rate is not None and fantasy_cat in SKATER_WEIGHTS:
            per_game = rate * toi_fraction
            total += per_game * SKATER_WEIGHTS[fantasy_cat]

    # PIM if available
    pim_rate = per_60_predictions.get("pim_per_60")
    if pim_rate is not None:
        total += (pim_rate * toi_fraction) * SKATER_WEIGHTS["pim"]

    return total


def actual_fpts_from_per_60(stats: dict, toi: float) -> float:
    """Compute actual fantasy points from a single game's per-60 stats.

    Args:
        stats: dict with keys like goals_per_60, assists_per_60, etc.
        toi: actual TOI in minutes for that game
    """
    if toi <= 0:
        return 0.0

    toi_fraction = toi / 60.0
    total = 0.0

    mapping = {
        "goals_per_60": "goals",
        "total_assists_per_60": "assists",
        "shots_per_60": "shots",
        "hits_per_60": "hits",
        "shots_blocked_per_60": "blocks",
        "pim_per_60": "pim",
    }

    for stat_col, fantasy_cat in mapping.items():
        rate = stats.get(stat_col)
        if rate is not None and fantasy_cat in SKATER_WEIGHTS:
            per_game = rate * toi_fraction
            total += per_game * SKATER_WEIGHTS[fantasy_cat]

    return total

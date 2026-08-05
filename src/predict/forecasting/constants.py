"""Constants for the v2 forecasting model.

Defines feature columns, stat targets, EWMA half-lives, and situation
configurations. These are the central reference for what the model
predicts and what features it uses.
"""

# ======================================================================
# Rolling windows (non-overlapping, in games)
# ======================================================================
# Each window is a half-open slice [start, end) of games ordered
# most-recent-first. Windows are disjoint so no game contributes to
# multiple features, avoiding the recency double-counting that EWMA
# with overlapping half-lives caused.
#   L5:      games 0–4    (last week)
#   L6_15:   games 5–14   (past month minus last week)
#   L16_30:  games 15–29  (early-season / ~month before that)
# Windows are large enough to keep per-60 rates stable; shorter
# windows (e.g. L3) proved too noisy and caused XGBoost to over-fit.
# Season average remains as the long-term anchor; prior_ and blended_
# features handle cold-start when fewer than 30 games exist.
ROLLING_WINDOWS = [
    ("l5", 0, 5),
    ("l6_15", 5, 15),
    ("l16_30", 15, 30),
]

# ======================================================================
# Situation configurations
# ======================================================================
# Each situation gets its own model family. Keys match game_advanced_stats.situation.

SITUATION_CONFIGS = {
    "5v5": {
        "stats": ["goals", "assists", "shots", "hits", "blocks"],
        "min_toi_seconds": 300,   # 5 min — filter noisy short-TOI games
        "min_games": 10,          # games before player enters training
    },
    "pp": {
        "stats": ["goals", "assists", "shots"],
        "min_toi_seconds": 30,    # PP time can be short
        "min_games": 10,
        "min_season_avg_toi": 60, # only model players averaging >1 min PP/game
    },
    "pk": {
        "stats": ["goals", "assists", "shots", "hits", "blocks"],
        "min_toi_seconds": 30,
        "min_games": 10,
    },
    "other": {
        # Combined 4v4 + 3v3 + empty net + other.
        # Only model scoring stats — counts are too low for hits/blocks.
        "stats": ["goals", "assists"],
        "min_toi_seconds": 10,
        "min_games": 5,           # lower threshold since this is a catch-all
        "source_situations": ["4v4", "3v3", "other"],  # aggregate these rows
    },
}

# ======================================================================
# Individual stats to compute per-60 EWMA features for
# ======================================================================
# These are columns in game_advanced_stats. Each gets EWMA features
# at every half-life plus the season average.
INDIVIDUAL_RATE_STATS = [
    "goals",
    "first_assists",
    "second_assists",
    "shots",
    "ixg",
    "shot_attempts",
    "hits",
    "blocks",
    "penalties",
    "penalties_drawn",
]

# ======================================================================
# On-ice stats to compute per-60 EWMA features for
# ======================================================================
ON_ICE_RATE_STATS = [
    "cf",
    "ca",
    "xgf",
    "xga",
    "hdcf",
]

# ======================================================================
# Ratio stats (not per-60, computed from on-ice counts)
# ======================================================================
# These are computed as numerator / (numerator + denominator) per game,
# then averaged with EWMA.
RATIO_STATS = [
    ("cf_pct", "cf", "ca"),       # Corsi For %
    ("xgf_pct", "xgf", "xga"),   # Expected Goals For %
    ("ozs_pct", "oz_starts", "dz_starts"),  # Offensive Zone Start %
]

# Shooting percentage: goals / shots (special case — not a for/against ratio)
# Handled separately in the feature extractor.

# ======================================================================
# IPP regression constants
# ======================================================================
# Position mean IPP (from our data, 5v5 season-level):
# Forwards (C/L/R): ~0.35, Defensemen: ~0.22
IPP_POSITION_MEANS = {
    "C": 0.342,
    "L": 0.341,
    "R": 0.364,
    "D": 0.217,
}
IPP_DEFAULT_MEAN = 0.33  # fallback if position unknown

# Stabilization constant for IPP regression to mean.
# With k=20, after 20 games the raw and mean are weighted 50/50.
IPP_STABILIZATION_K = 20

# ======================================================================
# Prior season Bayesian blending constant
# ======================================================================
# Used for blended features: blended = (prior * k + current * gp) / (k + gp)
# At 0 GP: 100% prior. At k GP: 50/50. At 3k GP: 25% prior.
PRIOR_SEASON_BLEND_K = 20

# ======================================================================
# Stat targets: maps model output names to game_advanced_stats columns
# ======================================================================
# The model predicts per-60 rates. The target is computed at training
# time as: column_value / toi_seconds * 3600
STAT_TARGETS = {
    "goals": "goals",
    "assists": "assists",      # total assists (first + second)
    "shots": "shots",
    "hits": "hits",
    "blocks": "blocks",
}

# ======================================================================
# Per-stat feature filters
# ======================================================================
# Substrings that a feature name must match (any one) to be included for
# a given stat. Features matching none of the substrings are excluded.
# Universal features (opponent, game context, position, TOI, GP) are
# always included for every stat.
#
# This prevents cross-category noise (e.g. hits history predicting goals).

_UNIVERSAL_FEATURES = {
    "opp_", "is_home", "is_b2b", "days_rest", "is_forward", "is_center",
    "season_gp", "prior_gp", "toi", "prior_toi",
}

STAT_FEATURE_FILTERS = {
    "goals": {
        "goals", "first_assists", "second_assists", "assists",
        "shots", "ixg", "shot_attempts",
        "cf", "ca", "xgf", "xga", "hdcf",
        "cf_pct", "xgf_pct", "ozs_pct", "sh_pct",
        "ipp",
    },
    "assists": {
        "goals", "first_assists", "second_assists", "assists",
        "shots", "ixg", "shot_attempts",
        "hits",
        "cf", "ca", "xgf", "xga", "hdcf",
        "cf_pct", "xgf_pct", "ozs_pct",
        "ipp",
    },
    "shots": {
        "shots", "ixg", "shot_attempts",
        "hits",
        "cf", "ca", "xgf", "xga", "hdcf",
        "cf_pct", "xgf_pct", "ozs_pct",
    },
    "hits": {
        "hits",
    },
    "blocks": {
        "blocks",
        "cf", "ca", "xgf", "xga", "hdcf",
        "cf_pct", "xgf_pct", "ozs_pct",
    },
}


def feature_allowed_for_stat(feature_name: str, stat: str) -> bool:
    """Check if a feature should be included for a given stat model."""
    for u in _UNIVERSAL_FEATURES:
        if u in feature_name:
            return True
    allowed = STAT_FEATURE_FILTERS.get(stat)
    if allowed is None:
        return True
    for substr in allowed:
        if substr in feature_name:
            return True
    return False

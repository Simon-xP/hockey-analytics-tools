"""Constants for the v2 forecasting model.

Defines feature columns, stat targets, EWMA half-lives, and situation
configurations. These are the central reference for what the model
predicts and what features it uses.
"""

# ======================================================================
# EWMA half-lives (in games)
# ======================================================================
# Multiple decay rates let XGBoost pick the right one per stat.
# Half-life of 3 captures hot streaks; 15 captures true talent.
# Season average is also included (effectively infinite half-life).
EWMA_HALF_LIVES = [3, 5, 10, 15]

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

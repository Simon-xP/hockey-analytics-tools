"""Feature extractors for the v2 forecasting model.

All extractors read from game_advanced_stats (computed from NHL API
play-by-play data via the shift-event correlation engine). This replaces
the NST-dependent extractors in the v1 model.

Feature naming convention:
    ewma_{half_life}_{stat}     — EWMA at specific decay rate
    season_avg_{stat}           — season average (all prior games)
    prior_{stat}                — prior season aggregate
    blended_{stat}              — Bayesian blend of prior + current season
    ipp_regressed               — IPP regressed toward position mean
    opp_{stat}                  — opponent team stat
    is_home, is_b2b, days_rest  — game context
    is_forward, is_center       — position
"""

from datetime import date, timedelta
from collections import defaultdict

import numpy as np
from sqlalchemy import text

from src.predict.forecasting.constants import (
    ROLLING_WINDOWS,
    INDIVIDUAL_RATE_STATS,
    ON_ICE_RATE_STATS,
    RATIO_STATS,
    IPP_POSITION_MEANS,
    IPP_DEFAULT_MEAN,
    IPP_STABILIZATION_K,
    PRIOR_SEASON_BLEND_K,
)


# ======================================================================
# EWMA utility
# ======================================================================

def ewma(values: list[float], half_life: int) -> float:
    """Exponentially weighted moving average.

    Args:
        values: Values ordered most-recent-first.
        half_life: Number of observations for the weight to halve.

    Returns:
        Weighted average where recent values count more.
    """
    if not values:
        return float("nan")
    alpha = 1 - 0.5 ** (1 / half_life)
    weights = [(1 - alpha) ** i for i in range(len(values))]
    total_weight = sum(weights)
    if total_weight == 0:
        return float("nan")
    return sum(v * w for v, w in zip(values, weights)) / total_weight


def safe_per_60(count: float, toi_seconds: float) -> float:
    """Convert a raw count to a per-60 rate, handling zero TOI."""
    if toi_seconds <= 0:
        return float("nan")
    return count / toi_seconds * 3600


def safe_ratio(numerator: float, denominator: float) -> float:
    """Compute a ratio, handling zero denominator."""
    total = numerator + denominator
    if total <= 0:
        return float("nan")
    return numerator / total


# ======================================================================
# Data loading (shared across extractors)
# ======================================================================

def load_player_game_stats(
    session,
    player_id: int,
    situation: str,
    before_date: date,
    season_start_game_id: int | None = None,
) -> list[dict]:
    """Load a player's game_advanced_stats rows, ordered most-recent-first.

    Args:
        session: SQLAlchemy session.
        player_id: NHL player ID.
        situation: Situation filter (e.g., "5v5", "pp"). For "other" model,
            pass "other_combined" and it will aggregate 4v4+3v3+other.
        before_date: Only include games before this date.
        season_start_game_id: If provided, only include games >= this ID
            (for current-season-only queries).

    Returns:
        List of dicts with all stat columns, ordered by game date descending
        (most recent first).
    """
    # Handle the "other" combined situation
    if situation == "other_combined":
        sit_filter = "gas.situation IN ('4v4', '3v3', 'other')"
    else:
        sit_filter = f"gas.situation = '{situation}'"

    game_id_filter = ""
    if season_start_game_id is not None:
        game_id_filter = f"AND gas.game_id >= {season_start_game_id}"

    query = f"""
        SELECT gas.game_id, g.date as game_date, gas.toi_seconds,
               gas.goals, gas.assists, gas.first_assists, gas.second_assists,
               gas.points, gas.shots, gas.shot_attempts, gas.missed_shots,
               gas.blocked_shots, gas.hits, gas.blocks, gas.giveaways,
               gas.takeaways, gas.penalties, gas.penalties_drawn,
               gas.faceoff_wins, gas.faceoff_losses,
               gas.ixg, gas.cf, gas.ca, gas.ff, gas.fa, gas.sf, gas.sa,
               gas.gf, gas.ga, gas.xgf, gas.xga,
               gas.scf, gas.sca, gas.hdcf, gas.hdca,
               gas.oz_starts, gas.dz_starts, gas.nz_starts, gas.ipp
        FROM game_advanced_stats gas
        JOIN games g ON gas.game_id = g.game_id
        WHERE gas.player_id = :pid AND {sit_filter}
              AND g.date < :before_date AND gas.toi_seconds > 0
              {game_id_filter}
        ORDER BY g.date DESC
    """

    rows = session.execute(
        text(query), {"pid": player_id, "before_date": before_date}
    ).fetchall()

    columns = [
        "game_id", "game_date", "toi_seconds",
        "goals", "assists", "first_assists", "second_assists",
        "points", "shots", "shot_attempts", "missed_shots",
        "blocked_shots", "hits", "blocks", "giveaways",
        "takeaways", "penalties", "penalties_drawn",
        "faceoff_wins", "faceoff_losses",
        "ixg", "cf", "ca", "ff", "fa", "sf", "sa",
        "gf", "ga", "xgf", "xga",
        "scf", "sca", "hdcf", "hdca",
        "oz_starts", "dz_starts", "nz_starts", "ipp",
    ]

    return [dict(zip(columns, r)) for r in rows]


# ======================================================================
# Feature extractors
# ======================================================================

def _window_mean(series: list[float], start: int, end: int) -> float:
    """Mean of finite values in series[start:end]. NaN if no finite values."""
    clean = [v for v in series[start:end] if np.isfinite(v)]
    if not clean:
        return float("nan")
    return sum(clean) / len(clean)


def extract_rolling_features(games: list[dict]) -> dict[str, float]:
    """Extract non-overlapping rolling-window features plus season average.

    Uses disjoint windows (see ROLLING_WINDOWS) so the most recent games
    don't get multi-counted across multiple features.

    Args:
        games: Player's game stats, most-recent-first.

    Returns:
        Dict of feature_name -> value.
    """
    features = {}
    n = len(games)
    features["season_gp"] = float(n)

    if n == 0:
        return features

    # Precompute per-60 rates and ratios for each game
    per_60_series = {}  # stat_name -> [values most-recent-first]
    for stat in INDIVIDUAL_RATE_STATS:
        per_60_series[stat] = [
            safe_per_60(g[stat] or 0, g["toi_seconds"])
            for g in games
        ]

    for stat in ON_ICE_RATE_STATS:
        per_60_series[f"oi_{stat}"] = [
            safe_per_60(g[stat] or 0, g["toi_seconds"])
            for g in games
        ]

    ratio_series = {}
    for name, num, den in RATIO_STATS:
        ratio_series[name] = [
            safe_ratio(g[num] or 0, g[den] or 0)
            for g in games
        ]

    # Shooting percentage
    ratio_series["sh_pct"] = [
        (g["goals"] / g["shots"]) if g["shots"] and g["shots"] > 0 else float("nan")
        for g in games
    ]

    # Raw TOI (not per-60)
    toi_series = [float(g["toi_seconds"]) for g in games]

    # Non-overlapping window means
    for prefix, start, end in ROLLING_WINDOWS:
        for stat, series in per_60_series.items():
            val = _window_mean(series, start, end)
            if np.isfinite(val):
                features[f"{prefix}_{stat}"] = val

        for stat, series in ratio_series.items():
            val = _window_mean(series, start, end)
            if np.isfinite(val):
                features[f"{prefix}_{stat}"] = val

        toi_val = _window_mean(toi_series, start, end)
        if np.isfinite(toi_val):
            features[f"{prefix}_toi"] = toi_val

    # Season averages (all prior games) — long-term anchor
    for stat, series in per_60_series.items():
        clean = [v for v in series if np.isfinite(v)]
        if clean:
            features[f"season_avg_{stat}"] = sum(clean) / len(clean)

    for stat, series in ratio_series.items():
        clean = [v for v in series if np.isfinite(v)]
        if clean:
            features[f"season_avg_{stat}"] = sum(clean) / len(clean)

    features["season_avg_toi"] = sum(toi_series) / len(toi_series)

    return features


def extract_prior_season_features(
    session,
    player_id: int,
    situation: str,
    current_season_start_year: int,
) -> dict[str, float]:
    """Extract prior season aggregate features.

    Computes season-level per-60 rates from the prior season's
    game_advanced_stats rows.

    Args:
        session: SQLAlchemy session.
        player_id: NHL player ID.
        situation: The situation to query.
        current_season_start_year: e.g. 2025 for the 2025-26 season.

    Returns:
        Dict of prior_{stat} features.
    """
    prior_year = current_season_start_year - 1
    prior_start = prior_year * 1_000_000
    prior_end = (prior_year + 1) * 1_000_000

    if situation == "other_combined":
        sit_filter = "gas.situation IN ('4v4', '3v3', 'other')"
    else:
        sit_filter = f"gas.situation = '{situation}'"

    row = session.execute(
        text(f"""
            SELECT COUNT(*) as gp,
                   SUM(gas.toi_seconds) as total_toi,
                   SUM(gas.goals) as goals, SUM(gas.assists) as assists,
                   SUM(gas.shots) as shots, SUM(gas.ixg) as ixg,
                   SUM(gas.shot_attempts) as shot_attempts,
                   SUM(gas.hits) as hits, SUM(gas.blocks) as blocks,
                   SUM(gas.penalties) as penalties,
                   SUM(gas.first_assists) as first_assists,
                   SUM(gas.second_assists) as second_assists,
                   SUM(gas.cf) as cf, SUM(gas.ca) as ca,
                   SUM(gas.xgf) as xgf, SUM(gas.xga) as xga,
                   SUM(gas.hdcf) as hdcf,
                   SUM(gas.oz_starts) as oz_starts,
                   SUM(gas.dz_starts) as dz_starts,
                   SUM(gas.points) as points, SUM(gas.gf) as gf
            FROM game_advanced_stats gas
            WHERE gas.player_id = :pid AND {sit_filter}
                  AND gas.game_id >= :start AND gas.game_id < :end
                  AND gas.toi_seconds > 0
        """),
        {"pid": player_id, "start": prior_start, "end": prior_end},
    ).fetchone()

    features = {}

    if not row or not row[0] or row[0] == 0:
        return features

    gp = row[0]
    total_toi = row[1] or 0

    features["prior_gp"] = float(gp)
    features["prior_toi_per_gp"] = total_toi / gp if gp > 0 else 0

    if total_toi > 0:
        col_map = {
            "goals": row[2], "assists": row[3], "shots": row[4],
            "ixg": row[5], "shot_attempts": row[6], "hits": row[7],
            "blocks": row[8], "penalties": row[9],
            "first_assists": row[10], "second_assists": row[11],
            "cf": row[12], "ca": row[13], "xgf": row[14], "xga": row[15],
            "hdcf": row[16],
        }

        for stat, val in col_map.items():
            features[f"prior_{stat}"] = safe_per_60(val or 0, total_toi)

        # Ratios
        cf = row[12] or 0
        ca = row[13] or 0
        features["prior_cf_pct"] = safe_ratio(cf, ca)

        xgf = row[14] or 0
        xga = row[15] or 0
        features["prior_xgf_pct"] = safe_ratio(xgf, xga)

        oz = row[17] or 0
        dz = row[18] or 0
        features["prior_ozs_pct"] = safe_ratio(oz, dz)

        # Shooting %
        goals = row[2] or 0
        shots = row[4] or 0
        features["prior_sh_pct"] = goals / shots if shots > 0 else float("nan")

        # IPP
        points = row[19] or 0
        gf = row[20] or 0
        features["prior_ipp"] = points / gf if gf > 0 else float("nan")

    return features


def extract_blended_features(
    rolling_features: dict[str, float],
    prior_features: dict[str, float],
    k: int = PRIOR_SEASON_BLEND_K,
) -> dict[str, float]:
    """Bayesian blend of prior season and current season features.

    blended = (prior * k + current * gp) / (k + gp)

    At 0 GP: 100% prior. At k GP: 50/50. At 3k GP: 25% prior.
    Provides smooth cold-start coverage.
    """
    features = {}
    gp = rolling_features.get("season_gp", 0)

    if gp == 0 and not prior_features:
        return features

    prior_weight = k / (k + gp) if (k + gp) > 0 else 1.0
    current_weight = 1 - prior_weight

    # Blend matching stats
    stats_to_blend = [
        "goals", "assists", "shots", "ixg", "shot_attempts",
        "hits", "blocks", "first_assists", "second_assists",
        "cf", "ca", "xgf", "xga", "hdcf",
    ]

    for stat in stats_to_blend:
        prior_val = prior_features.get(f"prior_{stat}")
        current_val = rolling_features.get(f"season_avg_{stat}")

        if prior_val is not None and current_val is not None:
            if np.isfinite(prior_val) and np.isfinite(current_val):
                features[f"blended_{stat}"] = (
                    prior_val * prior_weight + current_val * current_weight
                )
        elif prior_val is not None and np.isfinite(prior_val):
            features[f"blended_{stat}"] = prior_val
        elif current_val is not None and np.isfinite(current_val):
            features[f"blended_{stat}"] = current_val

    return features


def extract_ipp_features(
    games: list[dict],
    position: str,
) -> dict[str, float]:
    """Extract IPP features with regression toward position mean.

    IPP is noisy game-to-game, so we only use long windows (10, 15)
    plus the season aggregate. We regress toward the position mean
    to handle small samples.
    """
    features = {}

    if not games:
        return features

    # Compute per-game IPP (only games with GF > 0)
    ipp_values = []
    total_points = 0
    total_gf = 0

    for g in games:
        gf = g["gf"] or 0
        pts = g["points"] or 0
        total_points += pts
        total_gf += gf
        if gf > 0:
            ipp_values.append(pts / gf)

    # Season-level IPP
    season_ipp = total_points / total_gf if total_gf > 0 else float("nan")

    # Regressed IPP
    position_mean = IPP_POSITION_MEANS.get(position, IPP_DEFAULT_MEAN)
    n = len(games)
    k = IPP_STABILIZATION_K

    if np.isfinite(season_ipp):
        features["ipp_regressed"] = (
            n * season_ipp + k * position_mean
        ) / (n + k)
    else:
        features["ipp_regressed"] = position_mean

    features["ipp_season_raw"] = season_ipp

    # EWMA at long windows only
    if ipp_values:
        for hl in [10, 15]:
            features[f"ipp_ewma_{hl}"] = ewma(ipp_values, hl)

    return features


def extract_opponent_features(
    session,
    opponent_team_id: int,
    situation: str,
    before_date: date,
    half_life: int = 10,
) -> dict[str, float]:
    """Extract opponent team-level stats as features."""
    features = {}

    # Mirror situation: when predicting PP, use opponent's PK allowances
    opp_situation = {"pp": "pk", "pk": "pp"}.get(situation, situation)

    # Team-level per-game stats from game_advanced_stats.
    # SUM across players overcounts on-ice stats, so we take the MAX per
    # game (all on-ice players record the same ga/xga/sa for a given event).
    sit_rows = session.execute(
        text(f"""
            SELECT gas.game_id,
                   MAX(gas.ga) as ga, MAX(gas.xga) as xga,
                   MAX(gas.sa) as sa, MAX(gas.hdca) as hdca
            FROM game_advanced_stats gas
            JOIN games g ON gas.game_id = g.game_id
            WHERE gas.team_id = :tid AND gas.situation = :sit
                  AND g.date < :before_date AND gas.toi_seconds > 0
            GROUP BY gas.game_id
            ORDER BY MAX(g.date) DESC
            LIMIT 82
        """),
        {"tid": opponent_team_id, "sit": opp_situation, "before_date": before_date},
    ).fetchall()

    if sit_rows:
        features["opp_ga"] = ewma([r[1] or 0 for r in sit_rows], half_life)
        features["opp_xga"] = ewma([r[2] or 0 for r in sit_rows], half_life)
        features["opp_sa"] = ewma([r[3] or 0 for r in sit_rows], half_life)
        features["opp_hdca"] = ewma([r[4] or 0 for r in sit_rows], half_life)

    # Overall team scoring from game scores (situation-independent)
    game_rows = session.execute(
        text("""
            SELECT g.home_team_id, g.home_score, g.away_score, g.date
            FROM games g
            WHERE (g.home_team_id = :tid OR g.away_team_id = :tid)
                  AND g.date < :before_date
                  AND g.home_score IS NOT NULL
            ORDER BY g.date DESC
            LIMIT 82
        """),
        {"tid": opponent_team_id, "before_date": before_date},
    ).fetchall()

    if game_rows:
        ga_values = []
        gf_values = []
        for g in game_rows:
            is_home = g[0] == opponent_team_id
            ga_values.append(g[2] if is_home else g[1])
            gf_values.append(g[1] if is_home else g[2])

        features["opp_gaa"] = ewma(ga_values, half_life)
        features["opp_gfa"] = ewma(gf_values, half_life)

        days_since = (before_date - game_rows[0][3]).days
        features["opp_is_b2b"] = 1.0 if days_since == 1 else 0.0

    return features


def extract_game_context_features(
    session,
    player_id: int,
    team_id: int,
    game_date: date,
    home_team_id: int,
    as_of: date | None = None,
) -> dict[str, float]:
    """Extract game context features."""
    cutoff = min(game_date, as_of) if as_of is not None else game_date
    features = {}

    features["is_home"] = 1.0 if team_id == home_team_id else 0.0

    # Back-to-back and days rest
    last_game_date = session.execute(
        text("""
            SELECT MAX(g.date) FROM games g
            JOIN game_advanced_stats gas ON gas.game_id = g.game_id
            WHERE gas.player_id = :pid AND g.date < :gd
                  AND gas.situation = 'all' AND gas.toi_seconds > 0
        """),
        {"pid": player_id, "gd": cutoff},
    ).scalar()

    if last_game_date:
        days_rest = (cutoff - last_game_date).days
        features["is_b2b"] = 1.0 if days_rest == 1 else 0.0
        features["days_rest"] = float(min(days_rest, 7))  # cap at 7
    else:
        features["is_b2b"] = 0.0
        features["days_rest"] = 3.0  # default

    return features


def extract_position_features(position: str) -> dict[str, float]:
    """Extract position features."""
    return {
        "is_forward": 1.0 if position in ("C", "L", "R") else 0.0,
        "is_center": 1.0 if position == "C" else 0.0,
    }


# ======================================================================
# Full feature extraction for one player-game
# ======================================================================

def extract_all_features(
    session,
    player_id: int,
    situation: str,
    game_date: date,
    team_id: int,
    opponent_team_id: int,
    home_team_id: int,
    position: str,
    current_season_start_year: int,
    as_of: date | None = None,
) -> dict[str, float]:
    """Extract all features for one player-game prediction.

    This is the main entry point. Calls all individual extractors
    and merges their features into a single dict.

    Args:
        as_of: Temporal cutoff for data access. When provided, no data
            query may see games on or after this date. Defaults to
            game_date (standard single-game prediction). Set to the
            backtest decision date when forecasting future games.
    """
    cutoff = min(game_date, as_of) if as_of is not None else game_date

    query_situation = situation
    if situation == "other":
        query_situation = "other_combined"

    if situation in ("pk", "other"):
        season_start_gid = None
    else:
        season_start_gid = current_season_start_year * 1_000_000

    games = load_player_game_stats(
        session, player_id, query_situation, cutoff,
        season_start_game_id=season_start_gid,
    )

    features = {}

    # 1. Rolling EWMA features
    features.update(extract_rolling_features(games))

    # 2. Prior season features
    prior = extract_prior_season_features(
        session, player_id, query_situation, current_season_start_year
    )
    features.update(prior)

    # 3. Blended features (Bayesian prior + current)
    rolling_for_blend = {k: v for k, v in features.items()
                         if k.startswith("season_avg_") or k == "season_gp"}
    features.update(extract_blended_features(rolling_for_blend, prior))

    # 4. IPP features
    features.update(extract_ipp_features(games, position))

    # 5. Opponent features
    features.update(extract_opponent_features(
        session, opponent_team_id, situation, cutoff
    ))

    # 7. Game context
    features.update(extract_game_context_features(
        session, player_id, team_id, game_date, home_team_id, as_of=cutoff,
    ))

    # 8. Position
    features.update(extract_position_features(position))

    return features

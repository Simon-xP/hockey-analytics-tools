"""Empirical Bayes shrinkage estimator for rare event rates.

Used for PK goals/assists and Other situation scoring where per-game
events are too rare to model with regression. Instead of predicting
per-game rates with XGBoost, we estimate a player's "true" rate by
shrinking their observed rate toward the population average, weighted
by how much evidence (TOI) we have.

This is the Gamma-Poisson conjugate model:

    shrunk_rate = (observed_events + alpha) / (exposure + beta)

Where alpha and beta are estimated from the population distribution.
Equivalently:

    shrunk_rate = Z * observed_rate + (1 - Z) * population_rate

Where Z = exposure / (exposure + k) is the credibility weight and
k is the stabilization constant. With more exposure, we trust the
individual's rate more. With less, we lean on the population average.

This is mathematically equivalent to James-Stein estimation and
is the standard approach in actuarial credibility theory.

## Why this is used for PK/Other instead of XGBoost

PK goals never stabilize at the individual level within a career.
A top PK forward plays ~100-150 PK minutes/season and scores 0-1
SH goals. You'd need 20+ seasons to separate skill from luck.
Most serious hockey analytics sites don't try to predict SH
production at the individual game level — they regress heavily
toward the mean.

References:
- empiricalbayes.org (David Robinson)
- "Using Empirical Bayes to Define the Best NHL Shooter" (Analytics Vidhya)
- "Shrinkage and Empirical Bayes to Improve Inference" (kiwidamien)
"""

from datetime import date

import numpy as np
from sqlalchemy import text

from src.core.db import get_session


def compute_population_rates(
    session,
    situation: str,
    before_date: date,
    min_career_toi_seconds: float = 600,
) -> dict[str, dict]:
    """Compute population-level rate parameters for empirical Bayes.

    For each stat, computes:
    - population_rate: league-average per-60 rate
    - stabilization_k: how many seconds of TOI before individual rate
      gets 50% weight (estimated from population variance)

    Args:
        session: SQLAlchemy session.
        situation: "pk" or situation filter. For "other", uses combined.
        before_date: Only use data before this date.
        min_career_toi_seconds: Minimum total TOI to include a player
            in population parameter estimation.

    Returns:
        Dict of stat_name -> {"rate": float, "k": float}
    """
    if situation == "other":
        sit_filter = "gas.situation IN ('4v4', '3v3', 'other')"
    else:
        sit_filter = f"gas.situation = '{situation}'"

    # Get per-player aggregates
    rows = session.execute(
        text(f"""
            SELECT gas.player_id,
                   SUM(gas.goals) as goals, SUM(gas.assists) as assists,
                   SUM(gas.shots) as shots, SUM(gas.hits) as hits,
                   SUM(gas.blocks) as blocks, SUM(gas.toi_seconds) as toi
            FROM game_advanced_stats gas
            JOIN games g ON gas.game_id = g.game_id
            WHERE {sit_filter} AND g.date < :before_date AND gas.toi_seconds > 0
            GROUP BY gas.player_id
            HAVING SUM(gas.toi_seconds) >= :min_toi
        """),
        {"before_date": before_date, "min_toi": min_career_toi_seconds},
    ).fetchall()

    if not rows:
        return {}

    stats = {}
    for stat_idx, stat_name in [(1, "goals"), (2, "assists"), (3, "shots"),
                                 (4, "hits"), (5, "blocks")]:
        events = [r[stat_idx] or 0 for r in rows]
        tois = [r[6] for r in rows]
        rates = [e / t * 3600 for e, t in zip(events, tois) if t > 0]

        if not rates:
            continue

        # Population rate (weighted by TOI for more stable estimate)
        total_events = sum(events)
        total_toi = sum(tois)
        pop_rate = total_events / total_toi * 3600

        # Stabilization constant k: estimated from the population variance.
        # k = population_mean / population_variance_of_rates * 3600
        # Higher variance → lower k (less shrinkage needed)
        # Lower variance → higher k (more shrinkage, everyone is similar)
        rate_variance = np.var(rates)
        if rate_variance > 0 and pop_rate > 0:
            # k in seconds of TOI for 50% credibility
            # Derived from Gamma-Poisson: k ≈ pop_rate / rate_variance * 3600
            k = pop_rate / rate_variance * 3600
            # Clamp to reasonable range
            k = max(300, min(k, 36000))  # 5 min to 10 hours
        else:
            k = 3600  # default: 1 hour of TOI for 50% weight

        stats[stat_name] = {"rate": pop_rate, "k": k}

    return stats


def estimate_player_rate(
    session,
    player_id: int,
    situation: str,
    stat: str,
    before_date: date,
    population_params: dict | None = None,
) -> tuple[float, float]:
    """Estimate a player's true per-60 rate using empirical Bayes shrinkage.

    Args:
        session: SQLAlchemy session.
        player_id: NHL player ID.
        situation: "pk", "other", etc.
        stat: "goals", "assists", etc.
        before_date: Only use data before this date.
        population_params: Pre-computed population parameters (optional,
            computed if not provided).

    Returns:
        (shrunk_rate, credibility_weight) where:
            shrunk_rate: estimated per-60 rate (blended individual + population)
            credibility_weight: 0-1 indicating how much we trust the individual
                rate (Z). Higher = more evidence = more trust in individual rate.
    """
    if population_params is None:
        population_params = compute_population_rates(session, situation, before_date)

    params = population_params.get(stat)
    if not params:
        return (0.0, 0.0)

    pop_rate = params["rate"]
    k = params["k"]

    # Get player's aggregate stats
    if situation == "other":
        sit_filter = "gas.situation IN ('4v4', '3v3', 'other')"
    else:
        sit_filter = f"gas.situation = '{situation}'"

    row = session.execute(
        text(f"""
            SELECT SUM(gas.{stat}) as events, SUM(gas.toi_seconds) as toi
            FROM game_advanced_stats gas
            JOIN games g ON gas.game_id = g.game_id
            WHERE gas.player_id = :pid AND {sit_filter}
                  AND g.date < :before_date AND gas.toi_seconds > 0
        """),
        {"pid": player_id, "before_date": before_date},
    ).fetchone()

    if not row or not row[1] or row[1] <= 0:
        return (pop_rate, 0.0)

    player_events = row[0] or 0
    player_toi = row[1]

    # Individual rate
    individual_rate = player_events / player_toi * 3600

    # Credibility weight: Z = toi / (toi + k)
    z = player_toi / (player_toi + k)

    # Shrunk estimate
    shrunk_rate = z * individual_rate + (1 - z) * pop_rate

    return (shrunk_rate, z)


def blend_xgb_with_eb(
    xgb_rates: dict[str, float],
    eb_rates: dict[str, float],
    max_xgb_weight: float = 0.6,
    only_stats: list[str] | None = None,
) -> dict[str, float]:
    """Blend XGBoost per-60 predictions with empirical Bayes shrinkage.

    XGBoost can make extreme predictions for high-variance stats (notably
    PP assists for stars, where it doubles season rates). EB gives a stable
    credibility-weighted baseline that's the player's individual rate
    shrunk toward the population mean.

    Blend weight scales with EB credibility (Z), so:
    - Players with lots of evidence in the situation let XGBoost contribute
      up to max_xgb_weight of the final rate (XGBoost still informed by
      matchup, recent form, opponent quality, etc.)
    - Players with little evidence lean heavily on the EB rate (which
      is close to the population mean for them anyway)

    Always keeps at least (1 - max_xgb_weight) of the EB signal, acting
    as a regularizer that pulls XGBoost back from pathological extremes.

    Args:
        xgb_rates: dict of "{stat}_per60" -> float from the XGBoost model.
        eb_rates: dict of "{stat}_per60" and "{stat}_credibility" -> float
            from EmpiricalBayesPredictor.
        max_xgb_weight: Maximum weight XGBoost can receive. 0.6 means
            EB is always at least 40% of the final prediction.

    Returns:
        Blended dict of "{stat}_per60" -> float.
    """
    blended = {}
    for key, xgb_val in xgb_rates.items():
        if not key.endswith("_per60"):
            blended[key] = xgb_val
            continue
        stat = key[:-len("_per60")]
        if only_stats is not None and stat not in only_stats:
            blended[key] = xgb_val
            continue
        eb_val = eb_rates.get(f"{stat}_per60")
        if eb_val is None:
            blended[key] = xgb_val
            continue
        credibility = eb_rates.get(f"{stat}_credibility", 0.0)
        xgb_weight = max_xgb_weight * credibility
        blended[key] = xgb_weight * xgb_val + (1.0 - xgb_weight) * eb_val
    return blended


class EmpiricalBayesPredictor:
    """Predicts rare-event per-60 rates using empirical Bayes shrinkage.

    Used for PK goals/assists and Other situation scoring.
    Does NOT predict per-game — returns a stable per-60 rate estimate
    that should be multiplied by predicted TOI.

    The rate estimate changes slowly over time as more evidence accumulates.
    """

    def __init__(self, situation: str, stats: list[str]):
        self.situation = situation
        self.stats = stats
        self._pop_params_cache = None
        self._pop_params_date = None

    def predict(
        self,
        session,
        player_id: int,
        before_date: date,
    ) -> dict[str, float]:
        """Get shrunk per-60 rate estimates for a player.

        Returns dict of stat_per60 -> rate.
        """
        # Cache population params (recompute daily at most)
        if (self._pop_params_cache is None
                or self._pop_params_date != before_date):
            self._pop_params_cache = compute_population_rates(
                session, self.situation, before_date
            )
            self._pop_params_date = before_date

        results = {}
        for stat in self.stats:
            rate, z = estimate_player_rate(
                session, player_id, self.situation, stat,
                before_date, self._pop_params_cache,
            )
            results[f"{stat}_per60"] = rate
            results[f"{stat}_credibility"] = z

        return results

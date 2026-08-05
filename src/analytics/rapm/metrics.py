"""Linemate quality and deployment metrics derived from RAPM ratings.

These are real-time calculations from shift_segments + player_ratings.
Used as features in the opportunity model for transaction evaluation.

See docs/rapm-design.md Sections 5.1 and 7.1.
"""

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class OpportunityFeatures:
    linemate_quality: float | None
    linemate_quality_20g: float | None
    linemate_quality_delta: float | None
    own_rating: float | None
    deployment_gap: float | None
    elevator_nearby: float | None


def _load_ratings(
    session: Session,
    model_version: str = "rapm_v1_5v5",
) -> tuple[dict[int, float], dict[int, float]]:
    """Load offensive ratings and elevation scores from player_ratings."""
    rows = session.execute(
        text(
            "SELECT player_id, rating_off, elevation_off "
            "FROM player_ratings WHERE model_version = :mv"
        ),
        {"mv": model_version},
    ).fetchall()
    ratings = {r[0]: r[1] for r in rows}
    elevations = {r[0]: r[2] for r in rows if r[2] is not None}
    return ratings, elevations


def _get_recent_segments(
    session: Session,
    player_id: int,
    n_games: int,
    as_of: date | None = None,
) -> list[dict]:
    """Load 5v5 segments from a player's last N games."""
    date_filter = "AND g.date < :as_of" if as_of else ""
    params: dict = {"pid": player_id, "n_games": n_games}
    if as_of:
        params["as_of"] = str(as_of)

    rows = session.execute(
        text(f"""
            WITH recent_games AS (
                SELECT DISTINCT ss.game_id, g.date
                FROM shift_segments ss
                JOIN games g ON ss.game_id = g.game_id
                WHERE (:pid = ANY(ss.home_skater_ids)
                       OR :pid = ANY(ss.away_skater_ids))
                  AND ss.situation = '5v5'
                  {date_filter}
                ORDER BY g.date DESC
                LIMIT :n_games
            )
            SELECT ss.game_id, ss.duration_seconds,
                   ss.home_skater_ids, ss.away_skater_ids
            FROM shift_segments ss
            WHERE ss.game_id IN (SELECT game_id FROM recent_games)
              AND (:pid = ANY(ss.home_skater_ids)
                   OR :pid = ANY(ss.away_skater_ids))
              AND ss.situation = '5v5'
        """),
        params,
    ).fetchall()

    return [
        {
            "game_id": r[0],
            "duration_seconds": r[1],
            "home_skater_ids": r[2],
            "away_skater_ids": r[3],
        }
        for r in rows
    ]


def _compute_linemate_quality(
    segments: list[dict],
    player_id: int,
    ratings: dict[int, float],
) -> float | None:
    """Weighted-average offensive RAPM of a player's linemates."""
    total_weighted = 0.0
    total_duration = 0.0

    for seg in segments:
        dur = seg["duration_seconds"]
        if dur <= 0:
            continue

        if player_id in seg["home_skater_ids"]:
            teammates = [p for p in seg["home_skater_ids"] if p != player_id]
        elif player_id in seg["away_skater_ids"]:
            teammates = [p for p in seg["away_skater_ids"] if p != player_id]
        else:
            continue

        rated_teammates = [p for p in teammates if p in ratings]
        if not rated_teammates:
            continue

        avg_rating = sum(ratings[p] for p in rated_teammates) / len(rated_teammates)
        total_weighted += avg_rating * dur
        total_duration += dur

    if total_duration <= 0:
        return None
    return total_weighted / total_duration


def _compute_elevator_nearby(
    segments: list[dict],
    player_id: int,
    elevations: dict[int, float],
) -> float | None:
    """Max elevation score among a player's recent linemates."""
    teammate_elevations = set()
    for seg in segments:
        if player_id in seg["home_skater_ids"]:
            teammates = seg["home_skater_ids"]
        elif player_id in seg["away_skater_ids"]:
            teammates = seg["away_skater_ids"]
        else:
            continue
        for p in teammates:
            if p != player_id and p in elevations:
                teammate_elevations.add(elevations[p])

    if not teammate_elevations:
        return None
    return max(teammate_elevations)


def linemate_quality(
    session: Session,
    player_id: int,
    n_games: int = 5,
    as_of: date | None = None,
    model_version: str = "rapm_v1_5v5",
) -> float | None:
    """Compute linemate quality for a player over their last N games.

    Returns the duration-weighted average offensive RAPM rating of
    the player's linemates during 5v5 play.
    """
    ratings, _ = _load_ratings(session, model_version)
    segments = _get_recent_segments(session, player_id, n_games, as_of)
    if not segments:
        return None
    return _compute_linemate_quality(segments, player_id, ratings)


def opportunity_features(
    session: Session,
    player_id: int,
    as_of: date | None = None,
    model_version: str = "rapm_v1_5v5",
) -> OpportunityFeatures:
    """Compute all RAPM-derived opportunity features for a player.

    Features:
        linemate_quality: 5-game weighted-avg teammate RAPM
        linemate_quality_20g: 20-game weighted-avg teammate RAPM
        linemate_quality_delta: 5g minus 20g (positive = recent promotion)
        own_rating: player's RAPM offensive rating
        deployment_gap: linemate_quality - own_rating
        elevator_nearby: max elevation score among recent linemates
    """
    ratings, elevations = _load_ratings(session, model_version)

    segs_5 = _get_recent_segments(session, player_id, 5, as_of)
    segs_20 = _get_recent_segments(session, player_id, 20, as_of)

    lq_5 = _compute_linemate_quality(segs_5, player_id, ratings) if segs_5 else None
    lq_20 = _compute_linemate_quality(segs_20, player_id, ratings) if segs_20 else None

    delta = None
    if lq_5 is not None and lq_20 is not None:
        delta = lq_5 - lq_20

    own = ratings.get(player_id)

    gap = None
    if lq_5 is not None and own is not None:
        gap = lq_5 - own

    elev = _compute_elevator_nearby(segs_5, player_id, elevations) if segs_5 else None

    return OpportunityFeatures(
        linemate_quality=lq_5,
        linemate_quality_20g=lq_20,
        linemate_quality_delta=delta,
        own_rating=own,
        deployment_gap=gap,
        elevator_nearby=elev,
    )

"""Shift segment builder for RAPM.

Constructs shift segments from player_shifts, game_events, and
shot_attempts. A shift segment is a maximal time interval within a
period where the on-ice personnel do not change.

Segments are persisted to the shift_segments table for use by the
RAPM ridge regression model.
"""

import logging
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.models.shift_segments import ShiftSegment
from src.analytics.advanced_stats.shifts import (
    build_shift_index,
    build_situation_timeline,
    classify_situation,
    load_events,
    load_shifts,
    load_shot_xg,
    players_on_ice,
    time_to_seconds,
)

logger = logging.getLogger(__name__)

GOALIE_POSITION = "G"


def build_game_segments(
    session: Session,
    game_id: int,
    home_team_id: int,
    away_team_id: int,
) -> list[dict]:
    """Build shift segments for a single game.

    Returns a list of segment dicts (not yet persisted). Each dict has
    keys matching ShiftSegment columns.
    """
    events = load_events(session, game_id)
    shifts = load_shifts(session, game_id)
    shot_xg = load_shot_xg(session, game_id)

    if not events or not shifts:
        return []

    shift_team_ids = {s["team_id"] for s in shifts}
    home_shift_tid, away_shift_tid = _map_shift_teams(
        shift_team_ids, home_team_id, away_team_id
    )
    if home_shift_tid is None:
        logger.warning(
            "Game %s: couldn't map shift teams %s to game teams (%s, %s)",
            game_id, shift_team_ids, home_team_id, away_team_id,
        )
        return []

    shift_index = build_shift_index(shifts)
    situation_timeline = build_situation_timeline(events)

    goalie_ids = _identify_goalies(session, shifts)

    shot_lookup = _build_shot_lookup(session, game_id, home_shift_tid)

    score_by_period = _build_score_timeline(events, home_team_id)

    segments = []

    for period in (1, 2, 3):
        breakpoints = _get_breakpoints(shifts, period)
        if len(breakpoints) < 2:
            continue

        for i in range(len(breakpoints) - 1):
            t_start = breakpoints[i]
            t_end = breakpoints[i + 1]
            duration = t_end - t_start

            if duration < 2:
                continue

            t_mid = t_start + duration // 2
            situation = _get_situation_at_time(
                situation_timeline, period, t_mid, home_shift_tid
            )

            home_on_ice = players_on_ice(
                shift_index, home_shift_tid, period, t_mid
            )
            away_on_ice = players_on_ice(
                shift_index, away_shift_tid, period, t_mid
            )

            home_skaters = sorted(
                pid for pid in home_on_ice if pid not in goalie_ids
            )
            away_skaters = sorted(
                pid for pid in away_on_ice if pid not in goalie_ids
            )

            if situation == "5v5" and (
                len(home_skaters) != 5 or len(away_skaters) != 5
            ):
                continue

            home_xgf, away_xgf = _sum_xg_in_window(
                shot_lookup, period, t_start, t_end
            )

            home_lead = _score_at_time(
                score_by_period, period, t_start, home_team_id
            )

            segments.append({
                "game_id": game_id,
                "period": period,
                "start_seconds": t_start,
                "end_seconds": t_end,
                "duration_seconds": duration,
                "situation": situation,
                "score_state": home_lead,
                "home_skater_ids": home_skaters,
                "away_skater_ids": away_skaters,
                "home_xgf": home_xgf,
                "away_xgf": away_xgf,
            })

    return segments


def persist_segments(session: Session, segments: list[dict]) -> int:
    """Bulk-insert segments into the shift_segments table.

    Returns the number of rows inserted.
    """
    if not segments:
        return 0

    session.bulk_insert_mappings(ShiftSegment, segments)
    session.flush()
    return len(segments)


def build_and_persist_game(
    session: Session,
    game_id: int,
    home_team_id: int,
    away_team_id: int,
) -> int:
    """Build and persist segments for a single game. Returns row count."""
    segments = build_game_segments(session, game_id, home_team_id, away_team_id)
    return persist_segments(session, segments)


def build_segments_for_season(
    session: Session,
    season_start: str,
    season_end: str,
    batch_size: int = 50,
) -> int:
    """Build segments for all games in a date range that haven't been processed.

    Args:
        session: DB session.
        season_start: Start date (inclusive), e.g. "2024-10-01".
        season_end: End date (exclusive), e.g. "2025-07-01".
        batch_size: Commit every N games.

    Returns total segments created.
    """
    games = session.execute(
        text("""
            SELECT g.game_id, g.home_team_id, g.away_team_id
            FROM games g
            WHERE g.date >= :start AND g.date < :end
              AND EXISTS (
                  SELECT 1 FROM player_shifts ps WHERE ps.game_id = g.game_id
              )
              AND EXISTS (
                  SELECT 1 FROM game_events ge WHERE ge.game_id = g.game_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM shift_segments ss WHERE ss.game_id = g.game_id
              )
            ORDER BY g.date
        """),
        {"start": season_start, "end": season_end},
    ).fetchall()

    total = 0
    for i, (game_id, home_id, away_id) in enumerate(games):
        count = build_and_persist_game(session, game_id, home_id, away_id)
        total += count

        if (i + 1) % batch_size == 0:
            session.commit()
            logger.info(
                "Processed %d/%d games (%d segments so far)",
                i + 1, len(games), total,
            )

    session.commit()
    logger.info(
        "Done: %d games, %d total segments", len(games), total,
    )
    return total


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _map_shift_teams(
    shift_team_ids: set[int],
    home_team_id: int,
    away_team_id: int,
) -> tuple[int | None, int | None]:
    """Map shift team IDs to home/away.

    Handles cases where shift data uses different team IDs than the
    games table (e.g., Utah uses 68 in shifts but 59 in games).
    """
    if len(shift_team_ids) != 2:
        return None, None

    tid_a, tid_b = shift_team_ids

    if tid_a == home_team_id:
        return tid_a, tid_b
    if tid_b == home_team_id:
        return tid_b, tid_a
    if tid_a == away_team_id:
        return tid_b, tid_a
    if tid_b == away_team_id:
        return tid_a, tid_b

    # Neither matches directly — shouldn't happen but fall back to
    # treating the first as home (arbitrary but deterministic)
    return tid_a, tid_b


def _identify_goalies(session: Session, shifts: list[dict]) -> set[int]:
    """Get the set of goalie player IDs from the players in these shifts."""
    player_ids = list({s["player_id"] for s in shifts})
    if not player_ids:
        return set()

    rows = session.execute(
        text(
            "SELECT nhl_id FROM players WHERE nhl_id = ANY(:ids) "
            "AND position = :pos"
        ),
        {"ids": player_ids, "pos": GOALIE_POSITION},
    ).fetchall()

    return {r[0] for r in rows}


def _get_breakpoints(shifts: list[dict], period: int) -> list[int]:
    """Get sorted unique shift boundary timestamps for a period."""
    times = set()
    for s in shifts:
        if s["period"] != period:
            continue
        start_s = time_to_seconds(s["start_time"])
        end_s = time_to_seconds(s["end_time"])
        if end_s > start_s:
            times.add(start_s)
            times.add(end_s)
    return sorted(times)


def _get_situation_at_time(
    situation_timeline: dict,
    period: int,
    time_s: int,
    home_team_id: int,
) -> str:
    """Determine the situation at a specific time using the timeline."""
    timeline = situation_timeline.get(period, [])
    if not timeline:
        return "5v5"

    current_code = "1551"
    for t, code in timeline:
        if t <= time_s:
            current_code = code
        else:
            break

    return classify_situation(current_code, home_team_id, home_team_id)


def _build_shot_lookup(
    session: Session,
    game_id: int,
    home_shift_tid: int,
) -> list[dict]:
    """Load shot attempts with time info for xG aggregation per segment.

    Tags each shot as home/away using the shift-derived team ID mapping.
    """
    rows = session.execute(
        text("""
            SELECT period, game_seconds, team_id, xg
            FROM shot_attempts
            WHERE game_id = :gid AND xg IS NOT NULL AND period_type = 'REG'
        """),
        {"gid": game_id},
    ).fetchall()

    result = []
    for r in rows:
        period = r[0]
        game_seconds = r[1]
        period_offset = (period - 1) * 1200
        period_seconds = game_seconds - period_offset
        result.append({
            "period": period,
            "period_seconds": period_seconds,
            "is_home": r[2] == home_shift_tid,
            "xg": r[3],
        })
    return result


def _sum_xg_in_window(
    shots: list[dict],
    period: int,
    t_start: int,
    t_end: int,
) -> tuple[float, float]:
    """Sum xGF for home and away teams within a time window."""
    home_xgf = 0.0
    away_xgf = 0.0
    for s in shots:
        if s["period"] != period:
            continue
        if s["period_seconds"] < t_start or s["period_seconds"] >= t_end:
            continue
        if s["is_home"]:
            home_xgf += s["xg"]
        else:
            away_xgf += s["xg"]
    return home_xgf, away_xgf


def _build_score_timeline(
    events: list[dict],
    home_team_id: int,
) -> dict[int, list[tuple[int, int]]]:
    """Build a running score timeline from goal events.

    Returns {period: [(time_s, home_lead_after_goal), ...]}.
    """
    home_lead = 0
    timeline = defaultdict(list)

    for e in events:
        if e["event_type"] != "goal":
            continue
        period = e["period"]
        time_s = time_to_seconds(e["time_in_period"])
        if e["team_id"] == home_team_id:
            home_lead += 1
        else:
            home_lead -= 1
        timeline[period].append((time_s, home_lead))

    return dict(timeline)


def _score_at_period_start(
    score_timeline: dict[int, list[tuple[int, int]]],
    period: int,
) -> int:
    """Get the home lead at the start of a period."""
    home_lead = 0
    for p in range(1, period):
        goals = score_timeline.get(p, [])
        if goals:
            home_lead = goals[-1][1]
    return home_lead


def _score_at_time(
    score_timeline: dict[int, list[tuple[int, int]]],
    period: int,
    time_s: int,
    home_team_id: int,
) -> int:
    """Get the home lead at a specific time in a period."""
    home_lead = _score_at_period_start(score_timeline, period)
    for t, lead in score_timeline.get(period, []):
        if t <= time_s:
            home_lead = lead
        else:
            break
    return home_lead

"""Injury status and expected games missed.

Reads the latest report per player from `player_injuries` (Daily Faceoff +
LLM-parsed news) and turns it into the only two numbers the optimizer cares
about: when are they back, and how many of this week's games do they miss.

Filtered to reports published before `as_of`, so a backtest never learns
about an injury before it was announced.
"""

from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.core.models import PlayerInjury
from src.core.queries.schedule import ScheduleProvider


def load_injuries(
    session: Session,
    nhl_ids: set[int],
    as_of: date,
) -> dict[int, dict]:
    """Load current injury status for a set of players.

    Returns dict keyed by nhl_id with injury_status, soonest_return,
    latest_return. Only includes players who are actually injured
    (category='injury'). Filters to injuries reported before as_of
    for backtest safety.
    """
    from src.ingest.news.injuries import SEVERITY_DAY_DEFAULTS

    subq = (
        select(
            PlayerInjury.nhl_id,
            func.max(PlayerInjury.id).label("max_id"),
        )
        .where(
            PlayerInjury.nhl_id.in_(nhl_ids),
            PlayerInjury.scraped_at < as_of + timedelta(days=1),
        )
        .group_by(PlayerInjury.nhl_id)
        .subquery()
    )

    rows = (
        session.query(PlayerInjury)
        .join(subq, PlayerInjury.id == subq.c.max_id)
        .filter(
            or_(
                PlayerInjury.category == "injury",
                PlayerInjury.category.is_(None),
            )
        )
        .all()
    )

    result = {}
    for r in rows:
        if r.nhl_id is None:
            continue

        soonest, latest = compute_return_window(r, as_of)

        # If latest_return is in the past, player has likely returned
        if latest and latest < as_of:
            continue

        result[r.nhl_id] = {
            "injury_status": r.injury_status,
            "severity": r.severity,
            "soonest_return": soonest,
            "latest_return": latest,
        }

    return result


def compute_return_window(
    row: PlayerInjury,
    as_of: date,
) -> tuple[date | None, date | None]:
    """Compute return window from injury row, same logic as injuries.py."""
    if row.expected_return:
        return row.expected_return, row.expected_return

    anchor = row.news_date.date() if row.news_date else as_of

    if row.timeline_days_min and row.timeline_days_max:
        return (
            anchor + timedelta(days=row.timeline_days_min),
            anchor + timedelta(days=row.timeline_days_max),
        )

    from src.ingest.news.injuries import SEVERITY_DAY_DEFAULTS
    sev = row.severity or "unknown"
    if sev in SEVERITY_DAY_DEFAULTS:
        lo, hi = SEVERITY_DAY_DEFAULTS[sev]
        return anchor + timedelta(days=lo), anchor + timedelta(days=hi)

    if sev == "season":
        return None, None

    return None, None


def estimate_games_missed(
    session: Session,
    team_id: int,
    as_of: date,
    week_end: date,
    soonest_return: date | None,
    latest_return: date | None,
) -> int:
    """Estimate how many of the team's remaining games the player will miss.

    Uses the midpoint of the return window. If the player is season-ending
    (both None), they miss all remaining games.
    """
    if not team_id:
        return 0

    schedule = ScheduleProvider(session=session, as_of=as_of)
    games = schedule.get_team_games_in_range(team_id, as_of, week_end)

    if not games:
        return 0

    # Season-ending or unknown — miss everything
    if soonest_return is None and latest_return is None:
        return len(games)

    # Use midpoint of return window as expected return
    if soonest_return and latest_return:
        mid_days = (latest_return - soonest_return).days // 2
        expected_back = soonest_return + timedelta(days=mid_days)
    elif soonest_return:
        expected_back = soonest_return
    else:
        expected_back = latest_return

    missed = sum(1 for g in games if g["date"] < expected_back)
    return missed

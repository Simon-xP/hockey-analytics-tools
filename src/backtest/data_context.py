"""Backtest data context — immutable snapshot of all providers for a decision point.

Created once per decision date. All providers share the same as_of
and session, ensuring no data source can see beyond the temporal boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from src.core.queries.stats import StatsProvider
from src.core.queries.schedule import ScheduleProvider
from src.backtest.providers.yahoo import YahooProvider

if TYPE_CHECKING:
    from src.backtest.providers.forecast import ForecastProvider
    from src.backtest.providers.news import NewsProvider
    from src.backtest.providers.goalie import GoalieProvider


@dataclass(frozen=True)
class BacktestDataContext:
    """Immutable snapshot of all data providers for a single backtest decision point.

    All providers share the same as_of date and DB session.
    Providers not yet implemented are None.
    """

    as_of: date
    session: Session
    stats: StatsProvider
    schedule: ScheduleProvider
    yahoo: YahooProvider | None = None
    forecast: ForecastProvider | None = None
    news: NewsProvider | None = None
    goalie: GoalieProvider | None = None


def build_context(
    session: Session,
    as_of: date,
    *,
    league_key: str | None = None,
    team_name: str | None = None,
) -> BacktestDataContext:
    """Factory to build a BacktestDataContext with all available providers.

    Providers that require additional config (Yahoo, Forecast) are only
    created if their dependencies are available.
    """
    stats = StatsProvider(session=session, as_of=as_of)
    schedule = ScheduleProvider(session=session, as_of=as_of)

    yahoo = None
    if league_key and team_name:
        yahoo = YahooProvider(
            session=session,
            as_of=as_of,
            league_key=league_key,
            team_name=team_name,
        )

    return BacktestDataContext(
        as_of=as_of,
        session=session,
        stats=stats,
        schedule=schedule,
        yahoo=yahoo,
    )

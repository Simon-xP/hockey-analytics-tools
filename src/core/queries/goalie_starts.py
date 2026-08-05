"""Temporally gated reads of starting-goalie reports.

`goalie_starts` is an append-only log of observations (see the model
docstring). This module is the only thing that should read it, because it
is the only place that enforces the rule that makes those reads honest:
a decision made at time T may see reports observed strictly before T, and
nothing else.

Why the discipline is worth a module of its own: starting-goalie reports
are the single most leakage-prone input in the whole system. They arrive
late on game day, they are enormously informative, and a backtest that
reads them early will show a large fake edge on goalie streaming with no
symptom that anything is wrong. Every function here takes `as_of` and
there is no variant that does not.

`as_of` is a datetime, not a date, on purpose. Confirmations land during
the morning and afternoon of game day, so "the reports available on
Thursday" is not a well-formed question. "The reports available at 11am
Thursday" is.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text

# Probability that a start actually happens, given the source's wording.
#
# These are priors, not measurements. Daily Faceoff only serves the current
# day, so the observation history needed to calibrate them cannot be
# backfilled and has to accumulate going forward. Revisit once there is a
# season of appended observations; until then they are deliberately
# conservative, and `confidence` (below) reflects that they are assumed.
CONFIRMATION_PRIORS = {
    "confirmed": 0.97,
    "expected": 0.85,
    "likely": 0.75,
    "unconfirmed": 0.55,
}
DEFAULT_CONFIRMATION_PRIOR = 0.70

# How firm each tier is treated as being, separate from how likely it is.
# See the `confidence` discussion in docs/plans/weekly-optimizer/04a.
CONFIRMATION_CONFIDENCE = {
    "confirmed": 1.00,
    "expected": 0.75,
    "likely": 0.60,
    "unconfirmed": 0.35,
}
DEFAULT_CONFIRMATION_CONFIDENCE = 0.45


@dataclass(frozen=True)
class StartReport:
    """The latest report for one goalie in one game, as of some moment."""

    game_id: int
    nhl_id: int
    team_id: int
    confirmation: str | None
    confirmed: bool
    source: str
    observed_at: datetime

    @property
    def tier(self) -> str:
        return (self.confirmation or "unconfirmed").strip().lower()

    @property
    def p_start(self) -> float:
        return CONFIRMATION_PRIORS.get(self.tier, DEFAULT_CONFIRMATION_PRIOR)

    @property
    def confidence(self) -> float:
        return CONFIRMATION_CONFIDENCE.get(
            self.tier, DEFAULT_CONFIRMATION_CONFIDENCE
        )


def latest_start_reports(
    session,
    as_of: datetime,
    game_ids: list[int] | None = None,
    game_date: date | None = None,
) -> dict[tuple[int, int], StartReport]:
    """Most recent report per (game, goalie), observed strictly before `as_of`.

    Args:
        session: SQLAlchemy session.
        as_of: Decision time. Reports observed at or after this are invisible.
        game_ids: Restrict to these games.
        game_date: Restrict to games on this date.

    Returns:
        {(game_id, nhl_id): StartReport}. Empty when nothing was known yet,
        which is the correct answer for a decision made days in advance and
        must not be confused with "no goalie will start".
    """
    filters = ["gs.observed_at < :as_of"]
    params: dict = {"as_of": as_of}

    if game_ids:
        filters.append("gs.game_id = ANY(:gids)")
        params["gids"] = list(game_ids)
    if game_date is not None:
        filters.append("g.date = :gdate")
        params["gdate"] = game_date

    # DISTINCT ON keeps the newest observation per (game, goalie) that
    # survives the as_of filter. Backfilled rows are excluded: they were
    # scraped from a settled page and describe the outcome, not the report.
    rows = session.execute(
        text(f"""
            SELECT DISTINCT ON (gs.game_id, gs.nhl_id)
                   gs.game_id, gs.nhl_id, gs.team_id, gs.confirmation,
                   gs.confirmed, gs.source, gs.observed_at
            FROM goalie_starts gs
            JOIN games g ON g.game_id = gs.game_id
            WHERE {' AND '.join(filters)}
              AND gs.source NOT LIKE '%%_backfill'
            ORDER BY gs.game_id, gs.nhl_id, gs.observed_at DESC
        """),
        params,
    ).fetchall()

    return {
        (r[0], r[1]): StartReport(
            game_id=r[0], nhl_id=r[1], team_id=r[2], confirmation=r[3],
            confirmed=r[4], source=r[5], observed_at=r[6],
        )
        for r in rows
    }


def report_for_goalie(
    session,
    nhl_id: int,
    game_id: int,
    as_of: datetime,
) -> StartReport | None:
    """The latest report for one goalie in one game, or None."""
    reports = latest_start_reports(session, as_of=as_of, game_ids=[game_id])
    return reports.get((game_id, nhl_id))


def team_reports_for_game(
    session,
    team_id: int,
    game_id: int,
    as_of: datetime,
) -> list[StartReport]:
    """Every goalie on one team reported for one game, newest first.

    More than one is possible when a report changes which goalie is named;
    both names then have a live latest observation. The caller decides what
    to do about it, usually by trusting the more recent one.
    """
    reports = latest_start_reports(session, as_of=as_of, game_ids=[game_id])
    matching = [r for r in reports.values() if r.team_id == team_id]
    matching.sort(key=lambda r: r.observed_at, reverse=True)
    return matching

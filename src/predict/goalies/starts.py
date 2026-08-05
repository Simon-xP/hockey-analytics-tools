"""Probability a goalie starts, and how firm that probability is.

Two layers, because the information arrives late:

1. **Report layer.** Daily Faceoff, read through
   `src/core/queries/goalie_starts.py`, which returns only observations
   made strictly before the decision time. Available for today and
   sometimes tomorrow, and nothing further out.
2. **Prior layer.** Crease share over recent team games, for every day the
   reports cannot reach. Derived from `goalie_game_log`, which records who
   actually started every game back to 2015-16.

## `confidence` is not `p_start`

`p_start` answers "how likely is this to happen". `confidence` answers
"how much should you trust that number". They come apart exactly where it
matters.

A settled 1A/1B tandem and a team that just recalled a goalie from the AHL
can both produce `p_start = 0.5`. The first is a real asset to plan
around. The second is a reason to hold the transaction slot until Thursday
morning, when a confirmation may resolve it to 1.0 or 0.0.

Confidence is driven by how the estimate was reached, not by its value:

- a confirmed report is 1.0
- a softer report inherits its tier's firmness
- a crease-share estimate is scaled by how much history backs it and how
  settled the tandem looks

## Time discipline

Every function takes `as_of`. Report reads use a datetime, because
confirmations land during game day and "the reports available Thursday" is
not a well-formed question. Crease-share reads use the date and filter
`game_date < as_of`, never a `game_id` range. The known bug in
`src/optimize/goalies.py` is exactly the latter mistake: it bounds by
`game_id` and so sees the whole season regardless of `as_of`.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import text

from src.core.queries.goalie_starts import team_reports_for_game
from src.predict.goalies.constants import lookback_start

# Team games looked at when estimating crease share. Long enough to see a
# tandem pattern, short enough to react to a change in the pecking order.
CREASE_SHARE_WINDOW = 20

# Crease share is itself shrunk toward an even split, because a goalie
# recalled two days ago who started once is not a 100% starter.
CREASE_SHARE_PRIOR_GAMES = 6.0
CREASE_SHARE_PRIOR = 0.5

# Back-to-backs, measured on 2024-26 starts. The useful feature is not "is
# the team on a back-to-back" but "did *this* goalie go last night", which
# is nearly decisive:
#
#   goalie started night 1, n=844   P(start night 2) = 0.079
#   goalie was rested,      n=1956  P(start night 2) = 0.397
#
# The rested figure is below 0.5 because a team's season roster often
# includes three or more goalies, so "rested" is not the same as "the other
# half of a settled tandem". Crease share carries that distinction, which is
# why the rested case adjusts the share rather than replacing it.
B2B_STARTED_YESTERDAY_P = 0.08
B2B_RESTED_FLOOR = 0.40

# Confidence ceilings for model-derived estimates. A crease-share number is
# never as firm as a confirmation, no matter how much history backs it.
MAX_CREASE_SHARE_CONFIDENCE = 0.70
MIN_CONFIDENCE = 0.10


def season_start(as_of: date) -> date:
    """First day of the season containing `as_of`."""
    return lookback_start(as_of, 0)


@dataclass(frozen=True)
class StartProbability:
    p_start: float
    confidence: float
    source: str          # "confirmed", "report", "crease_share", "unknown"
    crease_share: float | None = None
    games_observed: int = 0
    on_b2b: bool = False


def crease_share(
    session,
    nhl_id: int,
    team_id: int,
    as_of: date,
    window: int = CREASE_SHARE_WINDOW,
) -> tuple[float, int]:
    """Fraction of the team's recent games this goalie started, this season.

    Returns (shrunk_share, games_observed). Gated on `game_date < as_of` and
    bounded below by the start of the current season.

    The season bound matters more than it looks. Without it, a query in
    October reaches back into last season and describes a tandem that may no
    longer exist: goalies change teams every summer, and a backup who was
    traded in would inherit their predecessor's crease share. Early in a
    season the correct answer is "few games observed, low confidence", which
    the caller already handles, rather than a confident number about the
    wrong pair of goalies.
    """
    rows = session.execute(
        text("""
            SELECT game_id,
                   BOOL_OR(goalie_id = :gid AND is_start) AS was_starter
            FROM goalie_game_log
            WHERE team_id = :tid
              AND game_date < :as_of AND game_date >= :season_start
            GROUP BY game_id
            ORDER BY MAX(game_date) DESC
            LIMIT :window
        """),
        {"gid": nhl_id, "tid": team_id, "as_of": as_of, "window": window,
         "season_start": season_start(as_of)},
    ).fetchall()

    if not rows:
        return CREASE_SHARE_PRIOR, 0

    n = len(rows)
    starts = sum(1 for r in rows if r[1])

    # Shrink toward an even split so a one-game sample cannot read as 1.0.
    shrunk = ((starts + CREASE_SHARE_PRIOR * CREASE_SHARE_PRIOR_GAMES)
              / (n + CREASE_SHARE_PRIOR_GAMES))
    return shrunk, n


def team_on_back_to_back(session, team_id: int, game_date: date) -> bool:
    """Did this team also play the day before?"""
    found = session.execute(
        text("""
            SELECT 1 FROM games
            WHERE date = :yesterday
              AND (home_team_id = :tid OR away_team_id = :tid)
            LIMIT 1
        """),
        {"yesterday": game_date - timedelta(days=1), "tid": team_id},
    ).scalar()
    return found is not None


def started_yesterday(
    session, nhl_id: int, game_date: date, as_of: date,
) -> bool:
    """Did this goalie start the night before `game_date`?

    Gated at `as_of` as well as at the date being asked about, so a
    projection made days in advance cannot learn who went last night. When
    planning Thursday from Monday this correctly returns False, and the
    caller falls back to crease share.
    """
    yesterday = game_date - timedelta(days=1)
    if yesterday >= as_of:
        return False
    found = session.execute(
        text("""
            SELECT 1 FROM goalie_game_log
            WHERE goalie_id = :gid AND game_date = :yesterday AND is_start
            LIMIT 1
        """),
        {"gid": nhl_id, "yesterday": yesterday},
    ).scalar()
    return found is not None


def estimate_start_probability(
    session,
    nhl_id: int,
    team_id: int,
    game_id: int,
    game_date: date,
    as_of: datetime,
) -> StartProbability:
    """Probability this goalie starts, using the best source available.

    `as_of` is a datetime because report visibility is time-of-day
    sensitive. Crease-share fallback uses its date part.
    """
    as_of_date = as_of.date()

    # --- Layer 1: reports ------------------------------------------------
    reports = team_reports_for_game(session, team_id, game_id, as_of)
    if reports:
        newest = reports[0]
        if newest.nhl_id == nhl_id:
            return StartProbability(
                p_start=newest.p_start,
                confidence=newest.confidence,
                source="confirmed" if newest.confirmed else "report",
            )
        # A different goalie on this team is reported. That is real
        # evidence against this one, and it is as firm as the report
        # naming the other goalie.
        return StartProbability(
            p_start=max(0.0, 1.0 - newest.p_start),
            confidence=newest.confidence,
            source="report",
        )

    # --- Layer 2: crease share ------------------------------------------
    share, games = crease_share(session, nhl_id, team_id, as_of_date)
    on_b2b = team_on_back_to_back(session, team_id, game_date)
    went_yesterday = (
        started_yesterday(session, nhl_id, game_date, as_of_date)
        if on_b2b else False
    )

    p = share
    if went_yesterday:
        # Nearly decisive: 8% across 844 measured cases.
        p = B2B_STARTED_YESTERDAY_P
    elif on_b2b:
        # Rested on a back-to-back. Their chance rises, but not to certainty:
        # "not the goalie who played" can be more than one person.
        p = max(share, B2B_RESTED_FLOOR)

    # Confidence rises with history and with how settled the tandem looks.
    # A genuine 50/50 split is less predictable than a clear 1A, so the
    # distance from an even split feeds in.
    history = min(1.0, games / float(CREASE_SHARE_WINDOW))
    decisiveness = abs(share - 0.5) * 2.0
    conf = MAX_CREASE_SHARE_CONFIDENCE * history * (0.4 + 0.6 * decisiveness)

    if went_yesterday:
        # Knowing they played last night is strong, specific evidence, and
        # unlike crease share it does not depend on having a long history.
        conf = max(conf, 0.80)
    elif on_b2b:
        # The pattern is real but the beneficiary is less certain.
        conf *= 0.85

    return StartProbability(
        p_start=max(0.0, min(1.0, p)),
        confidence=max(MIN_CONFIDENCE, conf) if (games or went_yesterday)
                   else MIN_CONFIDENCE,
        source="crease_share" if games else "unknown",
        crease_share=share,
        games_observed=games,
        on_b2b=on_b2b,
    )

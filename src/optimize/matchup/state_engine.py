"""Composition layer: matchup context in, `Posture` out.

The policy lives in `src/optimize/week/posture.py`. This module does the one
thing that module deliberately cannot: it computes the win probability, which
needs `matchup/win_probability.py`, and hands the result to the classifier.

It also owns `auto_importance`, which reads standings context and decides how
much this week is worth.
"""

from datetime import date

from src.optimize.matchup.win_probability import compute_win_probability
from src.optimize.models import (
    AggressionLevel,
    MatchupContext,
    WeekImportance,
    WinProbability,
)
from src.optimize.models.week import Posture
from src.optimize.week.posture import WinValuation, classify_posture


def determine_posture(
    ctx: MatchupContext,
    as_of: date,
    *,
    importance: WeekImportance | None = None,
    valuation: WinValuation | None = None,
) -> Posture:
    """Mode, window, and drop depth for this matchup. Pure, no I/O."""
    wp = compute_win_probability(ctx)
    return classify_posture(ctx, wp, as_of, importance=importance, valuation=valuation)


def determine_aggression(
    ctx: MatchupContext,
) -> tuple[AggressionLevel, WinProbability]:
    """Drop depth alone, for callers that do not want a window.

    Kept because `src/backtest/` and the existing tests take an
    `AggressionLevel` directly. Never returns `PREPARE`: a decided matchup now
    shows up as `PostureMode.PUNT`, which is a different axis. Use
    `determine_posture` for anything new.
    """
    wp = compute_win_probability(ctx)
    posture = classify_posture(ctx, wp, ctx.snapshot.week_start)
    return posture.aggression, wp


def auto_importance(
    my_rank: int,
    playoff_spots: int,
    is_playoff: bool,
    weeks_remaining: int | None = None,
    wins_from_cutoff: float | None = None,
    bye_spots: int = 0,
    wins_from_bye_cutoff: float | None = None,
) -> WeekImportance:
    """How much this week's matchup is worth to the season.

    Rank alone is a weak proxy in mid-season, so prefer the standings gap when
    the caller can supply it. A team one win out of the playoffs with six weeks
    left is in a very different position from one win out with one week left,
    and rank cannot tell them apart.

    Args:
        my_rank: Current standings position, 1-indexed.
        playoff_spots: How many teams make the playoffs.
        is_playoff: Whether this week is itself a playoff matchup.
        weeks_remaining: Regular-season weeks left including this one.
        wins_from_cutoff: Signed distance in wins to the last playoff spot.
            Positive means we hold a cushion, negative means we are chasing.
            Ties count as half a win. `None` falls back to rank.
        bye_spots: How many top seeds get a first-round bye. Zero disables it.
        wins_from_bye_cutoff: Signed distance in wins to the last bye seed.
            A team safely in the playoffs can still have a lot riding on a week
            that moves it toward a bye, which rank alone cannot see.
    """
    if is_playoff:
        return WeekImportance.CRAZY

    # How many wins can still plausibly change hands. One win per week is the
    # honest ceiling on our own record; the cutoff team can move the other way,
    # so a gap of up to `weeks_remaining` is live.
    swing = weeks_remaining if weeks_remaining is not None else None

    def _tier(gap: float | None) -> WeekImportance | None:
        """Importance implied by one signed standings gap, or None if unknown."""
        if gap is None:
            return None
        reachable = swing if swing is not None else 2
        if abs(gap) > max(1.0, float(reachable)):
            return WeekImportance.NEUTRAL
        if swing is not None and swing <= 2:
            return WeekImportance.CRAZY
        return WeekImportance.BIG

    tiers = [t for t in (_tier(wins_from_cutoff),) if t is not None]
    if bye_spots > 0:
        bye_tier = _tier(wins_from_bye_cutoff)
        if bye_tier is not None:
            tiers.append(bye_tier)

    if tiers:
        # Contention for either line is enough to make the week matter.
        for level in (WeekImportance.CRAZY, WeekImportance.BIG):
            if level in tiers:
                return level
        return WeekImportance.NEUTRAL

    # No standings gap supplied: fall back to rank.
    on_bubble = my_rank > (playoff_spots - 2)

    if on_bubble and weeks_remaining is not None and weeks_remaining <= 2:
        return WeekImportance.CRAZY

    if on_bubble:
        return WeekImportance.BIG

    if bye_spots > 0 and my_rank <= bye_spots + 2:
        # Near the bye line: finishing 1st is materially better than 4th.
        return WeekImportance.BIG

    return WeekImportance.NEUTRAL

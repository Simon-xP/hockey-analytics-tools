"""P5: posture — do we contest this week, over what window, and how deep may we reach?

One question, answered once per planning run, feeding everything else. A wrong
posture makes every downstream decision wrong in a way no amount of search
quality can recover, so this module is deliberately small and pure.

Three outputs:

    p_win + importance              -->  mode        CONTEST | PUNT
                                    -->  window      derived from mode alone
    p_win + importance + leverage   -->  aggression  CONSERVATIVE .. DESPERATE

Mode and depth are orthogonal. You can punt conservatively (protect the roster
while you set up next week) or punt aggressively (burn a fringe player to grab
someone with a five-game next week). The old `AggressionLevel.PREPARE` smuggled
a mode into the depth axis; nothing here emits it.

Aggression has exactly one job: it sets the drop floor in P4. It does not
weight, scale, or discount anything. If you find yourself wanting to multiply a
score by an aggression factor, the design has drifted.

## What sets the depth

Three things, and `p_win` on its own is none of them.

**How much we care.** `WeekImportance` shifts the whole ladder, it does not just
cap the top of it. At 45% we make ordinary moves in a nothing week, ordinary
moves in a week that matters, and start giving real pieces up in a playoff week.
See `LADDERS`. Caring more is also what keeps us contesting longer in both
directions, via `PUNT_BOUNDS`.

**Where the odds sit.** Ahead, we protect. Behind, we chase. That is the ladder
itself.

**Leverage.** This is the one `p_win` cannot express, and the calibration
scenarios prove it. Two states can sit at the same win probability, in the same
importance tier, and want opposite answers:

    Wednesday, +15, 14 games left vs 18   -> p_win 0.45, NORMAL
    Sunday,     -6,  3 games left vs  2   -> p_win 0.43, DESPERATE

Identical odds, identical stakes. What differs is how much win probability one
marginal fantasy point buys.

    sensitivity = phi(gap / sigma) / sigma

That is the derivative of `P(win)` with respect to our projected mean, and it
is the quantity the whole objective turns on. It peaks when the matchup is
close *and* the remaining slate is short, because sigma shrinks faster than the
gap does. On Sunday in a tie, one point can be worth four percentage points of
win probability, and roster damage you take now is damage you never pay for in
this matchup. That is worth reaching deep for. On Wednesday the same point buys
under two points, and you have four more days to find a better use of the add.

Note that this is *not* a day-of-week rule (see "There is no Sunday rule" in
`docs/plans/weekly-optimizer/00-overview.md`). Nothing here reads
`as_of.weekday()`. Sensitivity is a continuous property of the distribution;
short windows merely tend to produce high values of it.

## What a win is worth

`punt_high` and `punt_low` encode a judgment the code cannot derive: how much
season-long roster value is one matchup win worth? `ThresholdWinValuation`
answers it with fixed bounds keyed by importance tier.

The intended successor is `SimulatedWinValuation`: simulate the remaining
schedule, compute P(playoffs) with and without this win, and place the bounds
where the marginal playoff probability stops justifying roster damage. It is
not built. `WinValuation` exists so it can drop in without P6 noticing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping, Protocol

from src.optimize.models import (
    AggressionLevel,
    MatchupContext,
    WeekImportance,
    WinProbability,
)
from src.optimize.models.week import Posture, PostureMode

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
#
# Every number below was calibrated against
# `tests/optimize/matchup/test_posture_scenarios.py`, which encodes the posture
# a strong human manager would choose in ten hand-built matchup states. Change
# a number here and expect that file to tell you about it.


@dataclass(frozen=True)
class Ladder:
    """Where the depth boundaries sit for one importance tier.

    All three are lower bounds on `p_win`, read top down. `max_depth` clamps
    the result, which is how a tier opts out of DESPERATE entirely.
    """

    conservative: float  # above this: protect the roster, do nothing
    normal: float  # above this: ordinary upgrades
    aggressive: float  # above this: worth a real cost. Below: all in.
    max_depth: AggressionLevel


# How much we care about the win shifts the whole ladder, it does not merely cap
# it. Caring more means reaching deeper at the same odds, because the roster
# damage buys something worth more.
#
# Read across a row to see the tier's temperament; read down a column to see
# what caring more does to it. The `normal` column is the clearest: at 45% we
# make ordinary moves in a nothing week, ordinary moves in a big week, and start
# giving things up in a playoff week.
LADDERS: dict[WeekImportance, Ladder] = {
    # A week that changes nothing. Protect the roster early, never damage it.
    WeekImportance.NEUTRAL: Ladder(0.75, 0.35, 0.0, AggressionLevel.AGGRESSIVE),
    # The default. A win matters, the season matters more.
    WeekImportance.BIG: Ladder(0.85, 0.40, 0.05, AggressionLevel.DESPERATE),
    # Playoffs. There is no season after this to protect a roster for.
    WeekImportance.CRAZY: Ladder(0.92, 0.55, 0.20, AggressionLevel.DESPERATE),
}

# Leverage above which we reach as deep as the tier allows, regardless of where
# `p_win` sits on the ladder.
#
# 0.12 means "one player-game's worth of scoring uncertainty is worth twelve
# percentage points of this matchup". At that exchange rate a fringe drop is
# cheap. This is the axis `p_win` cannot express: rows 5 and 9 of the scenario
# table sit at 36% and 47% — nearly the same ladder rung — and want opposite
# answers, separated only by leverage (0.17 against 0.07).
#
# The threshold is DIMENSIONLESS, and deliberately so. See `win_leverage`: a
# threshold on the raw derivative would be denominated in 1/FPTS and would
# silently mean something different in every scoring system.
LEVERAGE_DESPERATE = 0.12

# `ThresholdWinValuation` bounds, keyed by importance. Caring more contests
# longer in both directions.
#
# BIG's 0.05 is the owner's number. Note that the scenario table's
# `down_fifty_on_monday` row prices out at 3.9% and therefore now punts; see the
# xfail note in `tests/optimize/matchup/test_posture_scenarios.py`. If that row
# should contest, the fix is a wider per-game variance model (P1) raising its
# `p_win`, not a lower threshold here.
PUNT_BOUNDS: dict[WeekImportance, tuple[float, float]] = {
    WeekImportance.NEUTRAL: (0.15, 0.85),
    WeekImportance.BIG: (0.05, 0.95),
    WeekImportance.CRAZY: (0.0, 1.0),  # unused; CRAZY short-circuits PUNT
}

# Length of the PUNT window. Always a full seven days: next Monday through
# next Sunday, when the current week ends on a Sunday.
PUNT_WINDOW_DAYS = 7

# Depth, ordered, so a tier's `max_depth` can clamp a level without a chain of
# special cases.
_DEPTH_ORDER: dict[AggressionLevel, int] = {
    AggressionLevel.CONSERVATIVE: 0,
    AggressionLevel.NORMAL: 1,
    AggressionLevel.AGGRESSIVE: 2,
    AggressionLevel.DESPERATE: 3,
}

# How each tier reads in a sentence a manager would say out loud.
_STAKES: dict[WeekImportance, str] = {
    WeekImportance.NEUTRAL: "a week that changes nothing",
    WeekImportance.BIG: "a week that matters",
    WeekImportance.CRAZY: "a week we cannot lose",
}


# ---------------------------------------------------------------------------
# What a win is worth
# ---------------------------------------------------------------------------


class WinValuation(Protocol):
    """How decided a matchup must be before we stop spending on it."""

    def punt_bounds(self, ctx: MatchupContext, importance: WeekImportance) -> tuple[float, float]:
        """Return `(punt_low, punt_high)` for this matchup."""
        ...


@dataclass(frozen=True)
class ThresholdWinValuation:
    """Fixed bounds keyed by importance tier.

    Deliberately ignores everything about the matchup except how much the week
    matters. `SimulatedWinValuation` is the successor that will not.
    """

    bounds: Mapping[WeekImportance, tuple[float, float]] | None = None

    def punt_bounds(self, ctx: MatchupContext, importance: WeekImportance) -> tuple[float, float]:
        table = self.bounds if self.bounds is not None else PUNT_BOUNDS
        return table.get(importance, PUNT_BOUNDS[WeekImportance.BIG])


DEFAULT_WIN_VALUATION = ThresholdWinValuation()


# ---------------------------------------------------------------------------
# Leverage
# ---------------------------------------------------------------------------


def win_sensitivity(gap: float, sigma: float) -> float:
    """Win probability bought per marginal fantasy point: `phi(gap/sigma)/sigma`.

    The derivative of `P(win)` with respect to our projected mean, and the
    quantity the whole objective turns on. Denominated in **1 / fantasy point**,
    so do not put a fixed threshold on it — use `win_leverage`.

    Zero when sigma is zero. No games remain, so no transaction can move the
    result, however close the score is.
    """
    if sigma <= 0:
        return 0.0
    z = gap / sigma
    return math.exp(-0.5 * z * z) / (math.sqrt(2 * math.pi) * sigma)


def win_leverage(gap: float, sigma: float, player_games: int) -> float:
    """`win_sensitivity` rescaled to be dimensionless, so a threshold can port.

    Multiplying the derivative by the typical scoring spread of a single
    player-game cancels the FPTS units:

        leverage = [phi(z) / sigma] * [sigma / sqrt(n)] = phi(z) / sqrt(n)

    Read it as "win probability bought per one player-game's worth of
    uncertainty". A league that scores goals 1 and nothing else, and a league
    that scores goals 100, produce identical leverage for the same matchup
    shape, because every point cancels. Only two things survive: how many
    standard deviations separate the teams, and how many player-games are left
    to swing.

    `player_games` is the count that actually feeds the projection — active
    lineup slots, `TeamProjection.remaining_fillable_games`, not bodies on the
    roster. Zero of them means nothing is left to move.
    """
    if sigma <= 0 or player_games <= 0:
        return 0.0
    sigma_per_game = sigma / math.sqrt(player_games)
    return win_sensitivity(gap, sigma) * sigma_per_game


# ---------------------------------------------------------------------------
# Mode, window, aggression
# ---------------------------------------------------------------------------


def _decide_mode(
    p_win: float,
    importance: WeekImportance,
    punt_low: float,
    punt_high: float,
) -> tuple[PostureMode, str]:
    """CONTEST unless the matchup is decided in either direction."""
    if importance == WeekImportance.CRAZY:
        return (
            PostureMode.CONTEST,
            "Playoff week — we contest this no matter how it looks.",
        )
    if p_win > punt_high:
        return (
            PostureMode.PUNT,
            f"Win locked at {p_win:.0%} — banking it and setting up next week.",
        )
    if p_win < punt_low:
        return (
            PostureMode.PUNT,
            f"Only {p_win:.0%} to win — writing this one off rather than " "burning assets on it.",
        )
    return (PostureMode.CONTEST, f"Live matchup at {p_win:.0%} — playing to win it.")


def derive_window(
    mode: PostureMode,
    as_of: date,
    week_start: date,
    week_end: date,
) -> tuple[date, date]:
    """Where the seven-day planning grid sits. Purely derived from mode.

    CONTEST covers what is left of the current matchup, which may be a partial
    week. PUNT slides past `week_end` entirely, so a player with three games
    left this week and none next week is correctly worth nothing.
    """
    if mode == PostureMode.CONTEST:
        return (max(as_of, week_start), week_end)
    next_start = week_end + timedelta(days=1)
    return (next_start, next_start + timedelta(days=PUNT_WINDOW_DAYS - 1))


def _decide_aggression(
    mode: PostureMode,
    p_win: float,
    sensitivity: float,
    importance: WeekImportance,
) -> tuple[AggressionLevel, tuple[str, ...]]:
    """How deep into our own roster we may reach. Sets the drop floor, nothing else."""
    if mode == PostureMode.PUNT:
        # Still a real decision — we have to choose what we will give up to
        # improve next week — but nothing about a decided matchup argues for
        # roster damage.
        return (
            AggressionLevel.CONSERVATIVE,
            ("Week is decided, so only clear upgrades are worth a roster spot.",),
        )

    ladder = LADDERS.get(importance, LADDERS[WeekImportance.BIG])
    stakes = _STAKES.get(importance, _STAKES[WeekImportance.BIG])
    reasoning: list[str] = []

    if p_win > ladder.conservative:
        # Never escalated on leverage: when we are this far ahead, the cheapest
        # way to protect the lead is to leave the roster alone.
        return (
            AggressionLevel.CONSERVATIVE,
            (f"Comfortably ahead at {p_win:.0%} — no reason to churn the roster.",),
        )

    if sensitivity >= LEVERAGE_DESPERATE:
        level = AggressionLevel.DESPERATE
        reasoning.append(
            f"Every extra fantasy point is worth {sensitivity * 100:.1f} percentage "
            "points of win probability — about as high-leverage as a matchup gets, "
            "so reach as deep as the floor allows."
        )
    elif p_win > ladder.normal:
        level = AggressionLevel.NORMAL
        reasoning.append(f"Even matchup at {p_win:.0%} in {stakes} — normal upgrades only.")
    elif p_win > ladder.aggressive:
        level = AggressionLevel.AGGRESSIVE
        reasoning.append(
            f"Behind at {p_win:.0%} in {stakes} — worth giving up something real to catch up."
        )
    else:
        level = AggressionLevel.DESPERATE
        reasoning.append(f"Down to {p_win:.0%} in {stakes} — spend whatever it takes.")

    if _DEPTH_ORDER[level] > _DEPTH_ORDER[ladder.max_depth]:
        level = ladder.max_depth
        reasoning.append(f"Capped at {level.value}: {stakes} is not worth damaging the roster for.")

    return level, tuple(reasoning)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def classify_posture(
    ctx: MatchupContext,
    wp: WinProbability,
    as_of: date,
    *,
    importance: WeekImportance | None = None,
    valuation: WinValuation | None = None,
) -> Posture:
    """Turn a matchup distribution into a mode, a window, and a drop depth.

    Pure: no database, no clock, no Yahoo. `wp` is passed in rather than
    computed so the planner can reuse the opponent projection it already has
    (`project_team_remaining` is expensive and its result does not vary across
    beam states).

    `importance` overrides `ctx.importance` when given, so the owner can force
    CRAZY on a specific week.
    """
    imp = importance if importance is not None else ctx.importance
    valuation = valuation or DEFAULT_WIN_VALUATION
    punt_low, punt_high = valuation.punt_bounds(ctx, imp)

    week_start = ctx.snapshot.week_start
    week_end = ctx.snapshot.week_end

    if as_of > week_end:
        # Nothing left of this matchup to influence. Not a calendar rule: the
        # contest window is empty, so contesting is not an available choice.
        mode = PostureMode.PUNT
        mode_reason = "Matchup window has closed — planning the next one."
    else:
        mode, mode_reason = _decide_mode(wp.p_win, imp, punt_low, punt_high)

    window_start, window_end = derive_window(mode, as_of, week_start, week_end)
    leverage = win_leverage(
        wp.projected_gap, wp.combined_sigma, remaining_player_games(ctx)
    )
    aggression, aggression_reasons = _decide_aggression(mode, wp.p_win, leverage, imp)

    reasoning = (
        mode_reason,
        f"Projected {wp.my_total:.1f} to {wp.opp_total:.1f} "
        f"({wp.projected_gap:+.1f}, give or take {wp.combined_sigma:.1f}).",
        f"Planning window {window_start:%a %b %d} to {window_end:%a %b %d}.",
        *aggression_reasons,
    )

    return Posture(
        mode=mode,
        window_start=window_start,
        window_end=window_end,
        aggression=aggression,
        p_win=wp.p_win,
        importance=imp,
        reasoning=reasoning,
    )

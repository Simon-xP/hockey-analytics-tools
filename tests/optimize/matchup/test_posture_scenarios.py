"""Calibration scenarios for posture.

Each row is a realistic matchup state and the posture a strong human manager
would choose in it. The thresholds in `src/optimize/week/posture.py` were fitted
to this table, so it is the specification, not a regression net: if a row looks
wrong to the league owner, change the row and re-fit, do not change the code to
match the old row.

Failures print the full derivation — projected totals, sigma, p_win, and
leverage — because a human has to read the output and judge whether the model
or the expectation is at fault.

## The state model

The table describes each matchup in the terms a manager thinks in: the current
score gap and how many player-games each side has left. Turning that into a
distribution needs two numbers.

`PER_GAME_MU` is what a rostered skater is worth on a night he plays, under
this league's scoring (G=3, A=2, PIM=0.3, SOG=0.3, HIT=0.4, BLK=0.5).

`PER_GAME_SIGMA` is the spread around it. A 4.5-point skater routinely posts 0
or 12, and teammates' nights are positively correlated because they share
games, so this is deliberately wider than the per-stat variances alone imply.
It is the single most load-bearing assumption in this file: it sets how fast a
deficit becomes hopeless, and therefore where PUNT bites. P1 owns the real
variance model and this should be replaced by it once it is fitted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pytest
from scipy.stats import norm

from src.optimize.matchup.state_engine import determine_posture
from src.optimize.models import (
    AggressionLevel,
    MatchupContext,
    MatchupSnapshot,
    PickupBoost,
    TeamProjection,
    WeekImportance,
    WinProbability,
)
from src.optimize.models.week import PostureMode
from src.optimize.week.posture import (
    LADDERS,
    LEVERAGE_DESPERATE,
    PUNT_BOUNDS,
    classify_posture,
    win_sensitivity,
)

PER_GAME_MU = 4.5
PER_GAME_SIGMA = 4.0

WEEK_START = date(2026, 1, 5)  # Monday
WEEK_END = date(2026, 1, 11)  # Sunday

DAYS = {
    "Mon": date(2026, 1, 5),
    "Tue": date(2026, 1, 6),
    "Wed": date(2026, 1, 7),
    "Thu": date(2026, 1, 8),
    "Fri": date(2026, 1, 9),
    "Sat": date(2026, 1, 10),
    "Sun": date(2026, 1, 11),
}


def build_context(
    gap: float,
    my_games: int,
    opp_games: int,
    importance: WeekImportance = WeekImportance.BIG,
    my_rank: int = 8,
) -> MatchupContext:
    """A matchup sitting `gap` points ahead with this many player-games left each.

    Pickup boosts are zero on both sides. Both teams have adds available and
    they roughly cancel; modelling them here would only add a knob that is not
    what these rows are testing.
    """
    my_earned = 100.0 + gap
    opp_earned = 100.0

    return MatchupContext(
        snapshot=MatchupSnapshot(
            my_team_key="t1",
            opp_team_key="t2",
            my_earned=my_earned,
            opp_earned=opp_earned,
            week_start=WEEK_START,
            week_end=WEEK_END,
            my_adds_remaining=4,
            opp_adds_remaining=4,
            yahoo_week=10,
        ),
        my_projection=TeamProjection(
            team_key="t1",
            earned=my_earned,
            mu_remaining=my_games * PER_GAME_MU,
            sigma_remaining=math.sqrt(my_games) * PER_GAME_SIGMA,
            remaining_games=my_games,
            remaining_fillable_games=my_games,
        ),
        opp_projection=TeamProjection(
            team_key="t2",
            earned=opp_earned,
            mu_remaining=opp_games * PER_GAME_MU,
            sigma_remaining=math.sqrt(opp_games) * PER_GAME_SIGMA,
            remaining_games=opp_games,
            remaining_fillable_games=opp_games,
        ),
        my_pickup_boost=PickupBoost(0.0, 0.0, 4),
        opp_pickup_boost=PickupBoost(0.0, 0.0, 4),
        importance=importance,
        my_rank=my_rank,
    )


@dataclass(frozen=True)
class Scenario:
    name: str
    day: str
    gap: float
    my_games: int
    opp_games: int
    importance: WeekImportance
    mode: PostureMode
    aggression: AggressionLevel
    why: str


BIG, CRAZY, NEUTRAL = WeekImportance.BIG, WeekImportance.CRAZY, WeekImportance.NEUTRAL
CONTEST, PUNT = PostureMode.CONTEST, PostureMode.PUNT
CONSERVATIVE = AggressionLevel.CONSERVATIVE
NORMAL = AggressionLevel.NORMAL
AGGRESSIVE = AggressionLevel.AGGRESSIVE
DESPERATE = AggressionLevel.DESPERATE

# Kept as a hand-aligned table on purpose: the columns are the specification,
# and reading down one of them is how you check the model is doing real work.
# Rows 9 and 10 exist to prove games-remaining asymmetry matters — gap alone is
# not the signal, gap relative to combined sigma is.
#
# name                                  day    gap  mine opp importance  mode    depth
# fmt: off
SCENARIOS = [
    Scenario(
        "tied_on_monday",                     "Mon",    0, 25, 25, BIG,     CONTEST, NORMAL,
        "Coin flip with a full week to work. No reason to reach yet."),
    Scenario(
        "down_fifty_on_monday",               "Mon",  -50, 25, 25, BIG,     CONTEST, AGGRESSIVE,
        "A big hole, but 50 player-games of variance is a lot of room. Chase it."),
    Scenario(
        "down_fifty_on_saturday",             "Sat",  -50,  4,  4, BIG,     PUNT,    CONSERVATIVE,
        "Same deficit, no games left to erase it. Stop burning assets."),
    Scenario(
        "down_fifty_on_saturday_in_playoffs", "Sat",  -50,  4,  4, CRAZY,   CONTEST, DESPERATE,
        "Playoffs are never conceded, however bad it looks."),
    Scenario(
        "close_on_the_final_day",             "Sun",   -6,  3,  2, BIG,     CONTEST, DESPERATE,
        "The highest-leverage state in the season: one point swings the matchup several "
        "percent and there is no tomorrow to protect the roster for."),
    Scenario(
        "close_on_a_meaningless_final_day",   "Sun",   -6,  3,  2, NEUTRAL, CONTEST, AGGRESSIVE,
        "Same leverage, but the week is not worth roster damage. Capped."),
    Scenario(
        "up_sixty_on_the_final_day",          "Sun",   60,  3,  3, BIG,     PUNT,    CONSERVATIVE,
        "Won. Bank it and spend the add on next week."),
    Scenario(
        "up_sixty_on_a_playoff_final_day",    "Sun",   60,  3,  3, CRAZY,   CONTEST, CONSERVATIVE,
        "Playoffs never punt, but a locked win is protected by doing nothing."),
    Scenario(
        "thin_lead_short_on_games",           "Wed",   15, 14, 18, BIG,     CONTEST, NORMAL,
        "The lead is thinner than it looks: four more opponent games erase it."),
    Scenario(
        "tied_but_out_of_games",              "Thu",    0,  8, 14, BIG,     CONTEST, AGGRESSIVE,
        "Level on points, six games light. Behind in everything but the score."),
]
# fmt: on


def _report(s: Scenario, posture) -> str:
    ctx = build_context(s.gap, s.my_games, s.opp_games, s.importance)
    my_total = ctx.my_projection.earned + ctx.my_projection.mu_remaining
    opp_total = ctx.opp_projection.earned + ctx.opp_projection.mu_remaining
    sigma = math.sqrt(ctx.my_projection.sigma_remaining**2 + ctx.opp_projection.sigma_remaining**2)
    projected_gap = my_total - opp_total
    return "\n".join(
        [
            "",
            f"scenario:   {s.name}",
            f"situation:  {s.day}, {s.gap:+.0f} on the scoreboard, "
            f"{s.my_games} player-games left vs {s.opp_games}, {s.importance.value}",
            f"rationale:  {s.why}",
            "",
            f"  projected     {my_total:.1f} to {opp_total:.1f}  ({projected_gap:+.1f})",
            f"  sigma         {sigma:.1f}",
            f"  p_win         {posture.p_win:.4f}",
            f"  leverage      {win_sensitivity(projected_gap, sigma):.4f} "
            f"win probability per fantasy point",
            "",
            f"  expected      {s.mode.value} / {s.aggression.value}",
            f"  actual        {posture.mode.value} / {posture.aggression.value}",
            "",
            "  posture said:",
            *[f"    {line}" for line in posture.reasoning],
            "",
        ]
    )


# Rows the owner has not reconciled yet. Strict, so that the day the underlying
# model changes and a row starts passing, this file says so out loud.
UNRESOLVED = {
    "down_fifty_on_monday": (
        "Punting at 5% is the owner's ruling, and under the variance model above "
        "this state prices out at 3.9%, so it punts. The row says it should be "
        "contested as 'very winnable'. Both cannot hold. Resolving it upward "
        "means a wider per-game variance model (P1's job) lifting p_win over 5%, "
        "not a lower punt threshold — the threshold is the judgment, the variance "
        "is the measurement, and it is the measurement that is in doubt."
    ),
}


def _param(s: Scenario):
    reason = UNRESOLVED.get(s.name)
    marks = [pytest.mark.xfail(reason=reason, strict=True)] if reason else []
    return pytest.param(s, marks=marks, id=s.name)


@pytest.mark.parametrize("scenario", [_param(s) for s in SCENARIOS])
def test_posture_scenario(scenario: Scenario):
    ctx = build_context(scenario.gap, scenario.my_games, scenario.opp_games, scenario.importance)
    posture = determine_posture(ctx, DAYS[scenario.day])

    actual = (posture.mode, posture.aggression)
    expected = (scenario.mode, scenario.aggression)
    assert actual == expected, _report(scenario, posture)


class TestWindow:
    """The window is derived from mode and nothing else."""

    def test_contest_window_covers_what_is_left_of_the_week(self):
        ctx = build_context(0, 25, 25)
        posture = determine_posture(ctx, DAYS["Thu"])
        assert posture.mode == PostureMode.CONTEST
        assert posture.window_start == DAYS["Thu"]
        assert posture.window_end == WEEK_END

    def test_punt_window_is_the_whole_of_next_week(self):
        ctx = build_context(60, 3, 3)
        posture = determine_posture(ctx, DAYS["Sun"])
        assert posture.mode == PostureMode.PUNT
        assert posture.window_start == date(2026, 1, 12)  # next Monday
        assert posture.window_end == date(2026, 1, 18)  # next Sunday

    def test_contest_window_never_starts_before_the_week_does(self):
        ctx = build_context(0, 25, 25)
        posture = determine_posture(ctx, date(2026, 1, 2))  # planning ahead
        assert posture.window_start == WEEK_START

    def test_a_closed_week_punts_rather_than_planning_an_empty_window(self):
        ctx = build_context(0, 0, 0)
        posture = determine_posture(ctx, date(2026, 1, 12))
        assert posture.mode == PostureMode.PUNT
        assert posture.window_start == date(2026, 1, 12)


class TestNoSundayRule:
    """Nothing branches on the day of the week."""

    def test_identical_distributions_get_identical_postures_on_every_day(self):
        postures = [determine_posture(build_context(-6, 3, 2), day) for day in DAYS.values()]
        modes = {p.mode for p in postures}
        levels = {p.aggression for p in postures}
        assert modes == {PostureMode.CONTEST}
        assert levels == {AggressionLevel.DESPERATE}

    def test_only_the_window_start_moves_with_the_day(self):
        starts = [
            determine_posture(build_context(-6, 3, 2), day).window_start for day in DAYS.values()
        ]
        assert starts == list(DAYS.values())


class TestBoundaries:
    """Exact threshold values, and the degenerate states around them.

    These go through `classify_posture` with a hand-built `WinProbability`
    rather than through `determine_posture`, because a `p_win` round-tripped
    through `ppf` and back through `cdf` lands a few ulps off the threshold and
    would make the assertions test floating point rather than policy.
    """

    @staticmethod
    def _at(
        p_win: float,
        importance=WeekImportance.BIG,
        sigma: float = 30.0,
    ):
        """Posture for a matchup sitting at exactly this `p_win`.

        Sigma is wide by default so the leverage escalation stays out of the way
        and each assertion exercises one threshold at a time.
        """
        ctx = build_context(0, 0, 0, importance)
        gap = sigma * float(norm.ppf(p_win)) if 0.0 < p_win < 1.0 else 0.0
        wp = WinProbability(
            p_win=p_win,
            projected_gap=gap,
            combined_sigma=sigma,
            my_total=100.0 + gap,
            opp_total=100.0,
        )
        return classify_posture(ctx, wp, DAYS["Mon"])

    def test_exactly_at_punt_high_still_contests(self):
        low, high = PUNT_BOUNDS[WeekImportance.BIG]
        posture = self._at(high)
        assert posture.mode == PostureMode.CONTEST
        assert posture.aggression == AggressionLevel.CONSERVATIVE

    def test_just_above_punt_high_punts(self):
        _, high = PUNT_BOUNDS[WeekImportance.BIG]
        assert self._at(high + 0.005).mode == PostureMode.PUNT

    def test_exactly_at_punt_low_still_contests(self):
        low, _ = PUNT_BOUNDS[WeekImportance.BIG]
        posture = self._at(low)
        assert posture.mode == PostureMode.CONTEST
        assert posture.aggression == AggressionLevel.DESPERATE

    def test_just_below_punt_low_punts(self):
        low, _ = PUNT_BOUNDS[WeekImportance.BIG]
        assert self._at(low - 0.005).mode == PostureMode.PUNT

    def test_exactly_at_the_conservative_threshold_is_not_yet_conservative(self):
        assert self._at(LADDERS[WeekImportance.BIG].conservative).aggression == NORMAL

    def test_exactly_at_the_normal_threshold_is_not_yet_normal(self):
        assert self._at(LADDERS[WeekImportance.BIG].normal).aggression == AGGRESSIVE

    def test_exactly_at_the_aggressive_threshold_is_desperate(self):
        ladder = LADDERS[CRAZY]
        assert self._at(ladder.aggressive, CRAZY).aggression == DESPERATE

    def test_exactly_at_the_leverage_threshold_escalates(self):
        """A tie whose sigma puts leverage exactly on the line."""
        sigma = norm.pdf(0.0) / LEVERAGE_DESPERATE
        posture = self._at(0.5, sigma=float(sigma))
        assert posture.aggression == AggressionLevel.DESPERATE

    def test_just_inside_the_leverage_threshold_does_not_escalate(self):
        sigma = norm.pdf(0.0) / LEVERAGE_DESPERATE
        posture = self._at(0.5, sigma=float(sigma) * 1.01)
        assert posture.aggression == AggressionLevel.NORMAL

    def test_leverage_never_escalates_a_comfortable_lead(self):
        """High leverage plus a big lead is still CONSERVATIVE, not DESPERATE."""
        posture = self._at(0.90, sigma=5.0)
        assert win_sensitivity(5.0 * float(norm.ppf(0.90)), 5.0) > LEVERAGE_DESPERATE
        assert posture.aggression == AggressionLevel.CONSERVATIVE

    def test_neutral_punts_earlier_than_big(self):
        assert self._at(0.10, BIG).mode == PostureMode.CONTEST
        assert self._at(0.10, NEUTRAL).mode == PostureMode.PUNT

    def test_crazy_never_punts_at_either_extreme(self):
        assert self._at(0.001, CRAZY).mode == PostureMode.CONTEST
        assert self._at(0.999, CRAZY).mode == PostureMode.CONTEST


class TestStakesSetTheDepth:
    """How much we care about the win moves the ladder, it does not just cap it."""

    _at = staticmethod(TestBoundaries._at)

    def test_the_same_odds_reach_deeper_as_the_week_matters_more(self):
        """12%: a nothing week is already gone, a playoff week is all-in."""
        assert self._at(0.12, NEUTRAL).mode == PostureMode.PUNT
        assert self._at(0.12, BIG).aggression == AGGRESSIVE
        assert self._at(0.12, CRAZY).aggression == DESPERATE

    def test_a_nothing_week_protects_the_roster_sooner(self):
        """80%: comfortable enough to stop touching a roster we do not need."""
        assert self._at(0.80, NEUTRAL).aggression == CONSERVATIVE
        assert self._at(0.80, BIG).aggression == NORMAL
        assert self._at(0.80, CRAZY).aggression == NORMAL

    def test_a_playoff_week_starts_chasing_from_an_even_matchup(self):
        """45%: level is fine in a normal week, not good enough in the playoffs."""
        assert self._at(0.45, NEUTRAL).aggression == NORMAL
        assert self._at(0.45, BIG).aggression == NORMAL
        assert self._at(0.45, CRAZY).aggression == AGGRESSIVE

    def test_a_nothing_week_never_reaches_desperate(self):
        for p_win in (0.20, 0.30, 0.50, 0.70):
            assert self._at(p_win, NEUTRAL).aggression != DESPERATE

    def test_the_ladders_are_monotone_in_stakes(self):
        """At any p_win, caring more never reaches less deep."""
        order = {CONSERVATIVE: 0, NORMAL: 1, AGGRESSIVE: 2, DESPERATE: 3}
        for i in range(16, 85):
            p_win = i / 100
            depths = [order[self._at(p_win, imp).aggression] for imp in (NEUTRAL, BIG, CRAZY)]
            assert depths == sorted(depths), f"p_win={p_win:.2f} gave {depths}"

    def test_no_games_left_and_level_is_a_normal_contest(self):
        """Sigma zero: nothing we do can move the result, so leverage is zero."""
        posture = determine_posture(build_context(0, 0, 0), DAYS["Sun"])
        assert posture.p_win == 0.5
        assert posture.mode == PostureMode.CONTEST
        assert posture.aggression == AggressionLevel.NORMAL

    def test_no_games_left_and_ahead_is_a_decided_win(self):
        posture = determine_posture(build_context(10, 0, 0), DAYS["Sun"])
        assert posture.p_win == 1.0
        assert posture.mode == PostureMode.PUNT

    def test_no_games_left_and_behind_is_a_decided_loss(self):
        posture = determine_posture(build_context(-10, 0, 0), DAYS["Sun"])
        assert posture.p_win == 0.0
        assert posture.mode == PostureMode.PUNT

    def test_empty_roster(self):
        """No players at all. Should not blow up, should not concede."""
        ctx = build_context(0, 0, 0)
        ctx.my_projection.roster_nhl_ids = []
        ctx.opp_projection.roster_nhl_ids = []
        posture = determine_posture(ctx, DAYS["Mon"])
        assert posture.mode == PostureMode.CONTEST
        assert posture.window_start == DAYS["Mon"]
        assert posture.window_end == WEEK_END


class TestPWinIsAlwaysPopulated:
    def test_punt_still_reports_why_we_conceded(self):
        posture = determine_posture(build_context(-50, 4, 4), DAYS["Sat"])
        assert posture.mode == PostureMode.PUNT
        assert 0.0 <= posture.p_win < 0.02
        assert any("write" in line.lower() or "%" in line for line in posture.reasoning)

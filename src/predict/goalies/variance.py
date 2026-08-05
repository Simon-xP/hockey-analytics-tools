"""Goalie-day distribution: mean and variance for the weekly optimizer.

The optimizer scores transactions by change in win probability, not by
expected points, so it needs a distribution from every player. A goalie
with a mean and no spread reads as a low-variance skater who happens to
score seven, which is the exact inverse of the truth, and the streaming
logic quietly stops working.

## The combination

A goalie-day is a Bernoulli start times a random outcome. With
`S ~ Bernoulli(p)` and `V` the points given a start (mean `m`, variance
`v`), day points are `S * V`:

    mean = p * m
    var  = p * v  +  p * (1 - p) * m**2
           \\_____/    \\______________/
           outcome     start uncertainty

Since `E[SV] = p*m` and `E[(SV)^2] = p*(v + m^2)`.

**The second term is usually the larger one** and is the whole reason
goalie streams behave differently from skater streams. Not starting means
zero points contributed that day, which is a real and large source of
spread that scaling the mean by `p` cannot express.

A goalie worth 7 per start with outcome sd 4.5:

    p = 1.0   mean 7.0   outcome 20.3   start  0.0   sd 4.5
    p = 0.5   mean 3.5   outcome 10.1   start 12.3   sd 4.7

The coin flip has half the mean and slightly *more* absolute variance.

## Why outcome_var is one global constant

Not a function of `start_value`, not per goalie, not per team. This is the
opposite of how skater variance works and the reason is structural.

A skater's output factors into rate times opportunity, and opportunity
(ice time) is a large, continuous, player-specific lever. A goalie has no
such lever. Opportunity is binary: sixty minutes or nothing. Nobody plays
a third-line goalie shift.

So the workload in a start is set by the matchup, not by who is in net.
Shots faced comes from the opponent's offense and the team's structure,
wins come from the team's offense, and the goalie adds a save-rate delta
on top. True-talent save rate across NHL starters spans a range worth
under one goal on thirty shots. The absolute spread of outcomes therefore
barely moves between a good goalie and a bad one: any goalie can post a
big night, better ones just do it more often.

Two consequences. A proportional or affine form has nothing real to latch
onto and mostly fits noise. And per-goalie variance is not estimable at
all: fifty starts a season is thin for a first moment and hopeless for a
second, so attempting it yields a table of noise that looks like insight.

The constant was fitted on walk-forward out-of-sample residuals of the
**total**, never by summing per-stat variances. Goalie stats are heavily
correlated through the same game (saves and goals against both scale with
shots faced, wins move inversely with goals against, a shutout is
literally zero goals against), so summing independent per-stat variances
is wrong by an amount that is hard to predict in either direction.

## Non-normality is fine

Points in a single start are lumpy and roughly bimodal, because the win
bonus splits the distribution into two clusters. That is not worth fixing.
These variances are only ever consumed inside a sum over a week (a few
goalie starts plus fifteen to twenty skater games), where the central
limit theorem does the smoothing. Fit the second moment and stop.

The interval helpers below do assume approximate normality and are
provided for calibration checking and for reporting, not for the
optimizer's arithmetic.
"""

import math
from dataclasses import dataclass
from datetime import date

from src.predict.goalies.constants import OUTCOME_VAR


@dataclass(frozen=True)
class GoalieDayForecast:
    """One goalie, one day: the full contract the optimizer consumes.

    `p_start` and `confidence` are deliberately separate fields. They look
    like the same thing and are not.

    `p_start = 0.5` means either "this is a genuine, settled timeshare" or
    "we have no idea what this team is doing". Identical mean, identical
    variance, opposite correct actions. The first is an asset you can plan
    around; the second is a reason to hold a transaction slot and wait,
    because a Daily Faceoff confirmation on Thursday morning may turn it
    into a 1.0 or a 0.0.

    The optimizer uses `confidence` to decide whether to fire a transaction
    now or defer it, and to report how sure it is of its own advice.
    """

    nhl_id: int
    game_date: date
    game_id: int | None

    p_start: float        # probability of starting, [0, 1]
    start_value: float    # expected fantasy points given a start
    outcome_var: float    # variance of points given a start
    confidence: float     # firmness of p_start, [0, 1]

    source: str = "model"  # "confirmed", "report", "model"

    @property
    def mean(self) -> float:
        """Expected points contributed on this day."""
        return self.p_start * self.start_value

    @property
    def variance(self) -> float:
        """Total variance: outcome spread plus start uncertainty."""
        return self.outcome_term + self.start_term

    @property
    def outcome_term(self) -> float:
        """p * v. Spread of the result, conditional on playing."""
        return self.p_start * self.outcome_var

    @property
    def start_term(self) -> float:
        """p * (1-p) * m^2. Spread from not knowing whether they play.

        Zero at p = 0 and p = 1, maximised at p = 0.5. Usually the larger
        of the two terms for anything short of a confirmed start.
        """
        return self.p_start * (1.0 - self.p_start) * self.start_value**2

    @property
    def sd(self) -> float:
        return math.sqrt(max(0.0, self.variance))

    def interval(self, coverage: float = 0.80) -> tuple[float, float]:
        """Approximate central interval. For reporting and calibration.

        Normal approximation, which understates the lumpiness of a single
        start. Honest enough for a coverage check and not used by the
        optimizer, which consumes the moments directly.
        """
        z = _normal_quantile(0.5 + coverage / 2.0)
        half = z * self.sd
        return (self.mean - half, self.mean + half)

    def prob_exceeds(self, threshold: float) -> float:
        """P(points > threshold), accounting for the chance of not starting.

        Written as a mixture rather than one normal, because the "did not
        start" outcome is an atom at exactly zero and smearing it into a
        bell curve is what makes ceiling comparisons wrong. Given a start,
        the conditional outcome is treated as normal around `start_value`.
        """
        if self.p_start <= 0.0:
            return 1.0 if threshold < 0 else 0.0

        sd_given_start = math.sqrt(max(1e-9, self.outcome_var))
        z = (threshold - self.start_value) / sd_given_start
        p_exceed_given_start = 1.0 - _normal_cdf(z)

        # The non-start branch contributes only if the threshold is below
        # zero, which it never is in practice.
        p_exceed_given_no_start = 1.0 if threshold < 0 else 0.0

        return (self.p_start * p_exceed_given_start
                + (1 - self.p_start) * p_exceed_given_no_start)


def combine(
    p_start: float,
    start_value: float,
    outcome_var: float = OUTCOME_VAR,
) -> tuple[float, float]:
    """The formula on its own, for callers that just want (mean, var)."""
    p = max(0.0, min(1.0, p_start))
    mean = p * start_value
    var = p * outcome_var + p * (1.0 - p) * start_value**2
    return mean, var


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _normal_quantile(q: float) -> float:
    """Inverse normal CDF via bisection. Exact enough, no scipy needed."""
    if q <= 0.0:
        return -math.inf
    if q >= 1.0:
        return math.inf
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _normal_cdf(mid) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0

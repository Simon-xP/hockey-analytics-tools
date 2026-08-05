# Goalie Variance for the Weekly Optimizer

Self-contained brief.
You do not need to read the other plans in this directory.

## Why this is needed

The weekly optimizer scores every transaction by how much it changes **win probability**, not by how many fantasy points it adds.
That means it needs a *distribution* from every player, not a point estimate: a mean and a standard deviation.

This matters most for goalies, and it is the mechanism behind a rule the owner has stated from experience:

> we might want to weigh goalie streams higher than player streams for individual nights, because goalies typically get higher variation in their scores

That weight is never written down anywhere.
Under a win-probability objective, high variance is valued correctly and automatically when we are behind, and correctly penalized when we are ahead.
But only if the variance number is honest.
With a mean and no spread, the optimizer treats a goalie as a low-variance skater who happens to score 7, which is the exact opposite of the truth, and the streaming logic quietly stops working.

So: the deliverable is a trustworthy variance, not a bigger number.

## What the optimizer needs from you

Per goalie, per day in the window:

| Output | Meaning |
|---|---|
| `p_start` | probability this goalie starts that day |
| `start_value` | expected fantasy points **given** that they start |
| `outcome_var` | variance of fantasy points **given** that they start |
| `confidence` | how firm the `p_start` estimate is, in `[0, 1]` |

The optimizer combines the first three itself.
`confidence` is used separately (see below).

## The combination formula

A goalie-day is a Bernoulli start times a random outcome.
Let `S ~ Bernoulli(p)` and let `V` be points given a start, with mean `m` and variance `v`.
Day points are `S * V`, and:

```
mean = p * m

var  = p * v  +  p * (1 - p) * m**2
       ^^^^^     ^^^^^^^^^^^^^^^^^^
       outcome   start uncertainty
```

Derivation, in case you want to check it: `E[SV] = p*m`, `E[(SV)^2] = p*(v + m^2)`, so `Var = p*v + p*(1-p)*m^2`.

Not starting means zero points contributed to the lineup that day, which is why the second term exists and is not optional.

### The second term is usually the bigger one

Take a goalie worth 7 points per start with an outcome standard deviation of 4.5.

| | mean | outcome term | start term | total sd |
|---|---|---|---|---|
| Confirmed starter, `p = 1.0` | 7.0 | 20.3 | 0.0 | 4.5 |
| Coin flip, `p = 0.5` | 3.5 | 10.1 | 12.3 | 4.7 |

The coin flip has **half the mean and slightly more absolute variance** than the confirmed starter.
A model that only multiplies the mean by `p_start` cannot express that, and will treat the two as interchangeable assets scaled by 2.

For comparison, a skater with a 3.5 mean has a standard deviation somewhere around 1.5 to 2.
So the coin-flip goalie carries roughly two and a half times the risk of a skater with the same expected output.
That gap is the entire reason goalie streams behave differently, and it lives almost entirely in the start-uncertainty term.

### Why this dominates the streaming decision

Because goalie quality is compressed (see below) but start probability is not, `p_start` usually matters more than which goalie you pick.

Two options for the same Thursday lineup slot, with outcome sd of 4.5 for both:

| Option | Value per start | `p_start` | Expected points | sd |
|---|---|---|---|---|
| Confirmed weak goalie | 5.0 | 1.00 | **5.0** | 4.5 |
| Elite goalie, timeshare | 8.0 | 0.50 | **4.0** | 5.1 |

The elite goalie is clearly better *per start*, 8 against 5.
As a Thursday slot he is worth less and riskier, because half the time he contributes nothing.

The optimizer's conclusion: take the confirmed weak goalie when neutral or ahead, take the timeshare elite when trailing late and you need the ceiling.
That is what experienced managers do, and it emerges from the two formula terms rather than from a rule.
It only emerges if `outcome_var` and the Bernoulli term are both present and honest.

## The trap: do not sum per-stat variances

If the per-start projection is built by predicting saves, goals against, wins, and shutouts individually and combining them with the scoring weights, that is fine for the **mean**.
It is wrong for the variance.

Goalie stats are heavily correlated through the same underlying game:

- saves and goals against both scale with shots faced
- wins move inversely with goals against
- a shutout is literally goals against equal to zero

Summing independent per-stat variances ignores all of that and will be badly off, in a direction that is hard to predict.

**Do not model the covariance structure.**
Fit the variance of the **total** directly and sidestep the problem:

1. Generate out-of-sample per-start projections across at least one full season, walk-forward so no projection sees its own game.
2. Compute the actual fantasy point total for each of those starts.
3. Bucket by projected value, and within each bucket compute `std(actual - projected)`.
4. Fit `outcome_var` as a function of `start_value`.

Note that step 3 measures **predictive** residual, not raw historical spread.
That is deliberate.
It folds in the model's own error, which is a real part of the uncertainty the optimizer should be reflecting.

### Fit one global constant

`outcome_var` is a **single number** for all goalie starts.
Not a function of `start_value`, not per goalie, not per team.

This is a deliberate decision and the reasoning is worth understanding, because it is the opposite of how skater variance works.

A skater's model factors into rate times opportunity, and opportunity (time on ice) is a large, continuous, player-specific lever.
A goalie has no such thing.
Opportunity is binary: sixty minutes or nothing.
Nobody plays a third-line goalie shift.

So the workload in any given start is set almost entirely by the matchup, not by who is in net.
Shots faced comes from the opponent's offense and the team's defensive structure.
Wins come from the team's offense.
The goalie contributes a save-percentage delta on top of that, and true-talent save percentage across NHL starters spans roughly `.895` to `.925`, which on thirty shots is under one goal.

Two consequences:

- **The absolute spread of outcomes barely moves between a good goalie and a bad one.** Any goalie can post a big night; better ones just do it more often. So a proportional or affine form has nothing real to latch onto and will mostly fit noise.
- **Per-goalie variance is not estimable.** About fifty starts a season is thin for a first moment and hopeless for a second. Attempting it produces a table of noise that looks like insight.

Fit the constant, report it, record the season it came from.
If you want to sanity check the decision, fit the affine form as well and confirm the slope is not distinguishable from zero. Then throw it away.

### Do not worry about per-game normality

The distribution of points in a single start is lumpy, roughly bimodal, because the win bonus splits it into two clusters.
That is fine and you should not try to fix it.
These variances are only ever consumed inside a sum over a whole week (a few goalie starts plus fifteen to twenty skater games), where the central limit theorem does the smoothing.
Fit the second moment correctly and stop there.

## Two things to verify in the existing per-start model

Both follow from the same fact as the section above: a start's value is mostly about the matchup, not the goalie.
Report findings either way. If they are already handled, say so and move on.

### 1. Recent form must be shrunk hard

Goalie quality is compressed.
Elite and poor NHL starters are separated by something like three points of expected value per start, against a game-to-game spread of four to five.

The consequence is uncomfortable but well established: **the noise band is wider than the talent band.**
Save percentage over ten starts has a standard error larger than the entire true-talent spread among NHL starters, so a ten-start sample is close to pure noise.
Over a full season it carries real but partial signal.

So any model leaning on trailing save percentage is chasing randomness, and it will be confidently wrong about which goalie to stream.

Requirement: trailing goalie performance is regressed toward a team and league baseline with an explicit, stated reliability weight that scales with shots faced, not starts.
The project already does exactly this for rare skater events via `src/predict/forecasting/empirical_bayes.py`.
Use the same shrinkage logic rather than inventing a second one.

State the reliability weight you land on in the module docstring.
If it implies that fifty starts of save percentage gets more than about half weight against the baseline, re-derive it, because that is almost certainly too aggressive.

### 2. Own-team offense should feed the win component

Wins are typically the largest single component of goalie fantasy scoring and the biggest discrete jump in the outcome distribution.
Wins are driven by **your team scoring goals**, which has nothing to do with the goalie.

Check whether own-team offense is an input to the per-start projection.
If the model only looks at opponent offense, it will systematically undervalue good goalies on high-scoring teams and overvalue them on defensive ones, and streaming decisions will be biased toward the wrong teams all season.

## `confidence` is not `p_start`

These look like the same thing and they are not.

`p_start = 0.5` can mean either:

- we know this is a genuine timeshare, or
- we have no information about this team's plans

Identical mean, identical variance, completely different decisions.
The first is a real asset you can plan around.
The second is a reason to hold a transaction slot and wait, because a Daily Faceoff confirmation arriving Thursday morning may convert it into a 1.0 or a 0.0.

The optimizer uses `confidence` for two things: deciding whether to fire a transaction now or defer it, and computing how sure it is about its own recommendation.
Give it an honest number.
A confirmed start is 1.0. A crease-share-derived estimate on a settled tandem is high. A crease-share estimate on a team that just recalled a goalie is low.

## Time discipline

Worth an explicit audit, because this is the failure mode that makes every backtest result meaningless without looking wrong.

Daily Faceoff confirmations arrive on game day, usually late morning to afternoon.
If the scraper **upserts** rather than appending rows with a scrape timestamp, then a backtest evaluating a Monday decision will read Thursday's confirmation, every goalie stream will look brilliant, and the measured edge will be pure leakage.

Requirements:

- Every start-probability record carries the timestamp it was observed.
- Every function that reads them takes an `as_of` date and filters strictly earlier.
- Same for the historical stats feeding `start_value` and `outcome_var`.

There is a known instance of exactly this bug in `src/optimize/goalies.py`, where `compute_goalie_game_log`, `compute_crease_share`, and `compute_opponent_softness` bound results by `game_id` range but never by date, so they see the full season regardless of `as_of`.
Do not replicate that pattern.

## Acceptance tests

- `goalie_sd_exceeds_skater_sd_at_equal_mean`. If this fails, nothing else matters.
- `coin_flip_is_riskier_than_a_confirmed_starter_of_equal_expected_value`. Compare a `p=0.5` goalie worth 7 per start against a `p=1.0` goalie worth 3.5 per start. Same mean. The coin flip must have materially higher variance. This is the test that proves the Bernoulli term is wired in.
- `start_term_vanishes_at_certainty`. `p = 0.0` and `p = 1.0` both contribute zero start uncertainty.
- `variance_peaks_at_intermediate_p`, for fixed `start_value`.
- `low_projection_goalies_are_not_understated`. Compare the fitted constant against measured residuals in the bottom projection quartile. A proportional form would fail here, which is why the constant is the requirement.
- `ceiling_favors_the_timeshare_elite`. Using the two options from the streaming table, compute `P(points > 7)` for each. The timeshare elite must be higher despite the lower mean. This is the "we need a ceiling" case expressed at the variance level, without needing the optimizer.
- `recent_form_is_shrunk`. A goalie with ten elite starts and no prior history projects close to the team and league baseline, not close to their trailing numbers. Assert the direction and roughly the magnitude.
- `own_team_offense_moves_the_projection`. Same goalie, same opponent, swap the goalie's own team between a high-scoring and a low-scoring one. The projection must change. If it does not, the win component is not wired to own-team offense.
- `calibration`: on held-out data, the fraction of actual start totals landing inside the model's 80% interval is between 0.75 and 0.85. If this fails, say so rather than tuning until it passes. A miscalibrated variance under a win-probability objective produces confidently wrong risk decisions, which is worse than no goalie support at all.
- Three leakage tests, one each for start probability, `start_value`, and `outcome_var`: set `as_of` before a stretch of games and confirm the outputs cannot see it.

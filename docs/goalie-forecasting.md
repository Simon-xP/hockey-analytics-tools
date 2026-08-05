# Goalie Forecasting

Scoping document for a goalie evaluation pipeline that mirrors `src/predict/forecasting/` (skaters).

## The one-line version

For skaters we predict **rate x volume** (per-60 stats x TOI).
For goalies we predict **outcome x opportunity**: how many fantasy points a goalie scores *if* they start, multiplied by the probability they actually start.

```
E[FPTS] = P(start) x E[FPTS | start]
```

Those two halves are different problems with different data, different models, and different refresh cadences, so they get built as two separate things that meet at the end.

---

## Part 1: Why goalies are not skaters

### Measurements from our own data

Everything below was computed from `shot_attempts` (11 seasons, 2015-16 through 2025-26, xG scored on every shot) and `games`.

**Split-half reliability within the 2024-25 season** (odd vs even games, 41 goalies with 30+ starts).
This asks: if I know how a goalie did in half their games, how well does that predict the other half?

| Metric | Split-half r |
|---|---|
| Save percentage | **0.09** |
| Goals saved above expected per 100 shots | **-0.05** |
| Shots faced per game | **0.45** |

**Season-over-season stability** (341 goalie-season pairs, 25+ starts in both).

| Metric | Year-over-year r |
|---|---|
| Save percentage | 0.30 |
| GSAx per 100 shots | 0.23 |
| Shots faced per game | 0.43 |

Read those two tables together and the whole design falls out:

1. **A goalie's save percentage over 20 games is almost pure noise.** Building an XGBoost model to predict next-game save percentage from recent save percentage would be fitting static.
2. **Save skill is real but small and slow.** It only shows up across full seasons (r = 0.30), and even then it explains under 10% of variance. It belongs in a heavily shrunk, career-weighted estimate, not a reactive rolling window.
3. **Workload is the most predictable thing about a goalie.** Shots faced is roughly as stable within a season as it is across seasons, because it is a property of the team in front of them, not the goalie.

### Era drift: the training window question

Measured from the built game log, starts only:

| Season | Starts | FPTS/start | Shots against | Save % |
|---|---|---|---|---|
| 2015-16 | 2,294 | 6.10 | 28.7 | .9154 |
| 2017-18 | 2,378 | 6.24 | 30.7 | .9125 |
| 2019-20 | 2,022 | 6.02 | 30.4 | .9091 |
| 2021-22 | 2,461 | 5.98 | 30.5 | .9079 |
| 2023-24 | 2,460 | 5.65 | 29.3 | .9042 |
| 2024-25 | 2,622 | 5.24 | 27.4 | .9000 |
| 2025-26 | 2,559 | 4.99 | 27.0 | .8959 |

A goalie start is worth 18% fewer fantasy points than it was eleven years ago, and the decline is monotonic. Two separate forces: league save percentage has fallen about 20 points, and shots against per start have fallen about 2.

This settles the training window question, and the answer is neither "all of it" nor "last four years":

- **Use all eleven seasons, but do not treat them as exchangeable.** Throwing away 2015-2021 discards roughly 60% of the sample, and the *relationships* being learned (a team that concedes chances concedes goals, a rested starter faces fewer shots) are stable even as the levels shift.
- **Handle the level shift explicitly.** Either add a season index as a feature, or normalise each target by its season mean and predict a relative rate, then multiply the current season's level back in. The second is cleaner: it stops the model from spending capacity on a trend it cannot extrapolate anyway.
- **Weight recent seasons more.** A sample weight decaying with season age gets most of the benefit of a short window without the variance cost of throwing data away.
- **Never evaluate against a global mean baseline.** A model trained on 2015-2023 and tested on 2025-26 will look good simply by being biased high on a baseline that is biased higher. Baselines must be season-relative.

The same caution applies to the save-quality shrinkage prior in 5b: the league mean it shrinks toward has to be the *current* season's mean, not the pooled eleven-season mean.

### Where the fantasy points actually come from

League weights (`src/core/scoring.py`): W = 3.3, GA = -1.25, SV = 0.28, SO = 2.3.

2024-25 actuals, per goalie start (2,680 starts, 10+ shots faced):

| Component | Average points |
|---|---|
| Saves | +6.89 |
| Goals against | -3.39 |
| Wins | +1.62 |
| Shutouts | +0.15 |
| **Total** | **5.26** (sd 4.10) |

Average start: 27.3 shots faced, .895 save percentage, 2.71 GA.

The scoring formula simplifies usefully. Since `saves = shots_against - GA`:

```
FPTS = 0.28 x shots_against  -  1.53 x GA  +  3.3 x win  +  2.3 x shutout
```

Every shot faced is worth +0.28 before anything happens.
Every goal allowed costs 1.53 (the 1.25 penalty plus the 0.28 save you did not get).

So the target decomposes into four things with completely different signal structures:

| Piece | Driven by | How predictable |
|---|---|---|
| Shots against | Team defence + opponent offence + pace | Good (r = 0.45) |
| Goals against | Shot quality faced, then a small goalie skill nudge | Quality is predictable, the nudge barely is |
| Win | Team strength vs opponent, home ice | Moderate, and it is a *team* model not a goalie model |
| Shutout | Falls out of the GA distribution | Derived, not modelled |

**This is the core architectural claim: do not regress on FPTS directly.
Model the four components and add them up.**
A single model on FPTS has to learn the team-strength signal, the pace signal, and the save-skill signal all at once from one noisy label, and the noisiest component (save percentage) will dominate the residual.

---

## Part 2: What data we have

### Already in the database

| Source | Table | Coverage |
|---|---|---|
| Shot-level events with `goalie_id` and `xg` | `shot_attempts` | 11 seasons, 2015-16 to 2025-26, ~1.48M shots |
| Goalie shifts (TOI, who started) | `player_shifts` | See gap table below |
| Scores for win/loss/shutout | `games` | Complete except 26 games in 2025-26 |
| Skater on-ice stats for team context | `game_advanced_stats` | Complete where shifts exist |
| Probable starters scraper | `src/ingest/daily_faceoff/scraper.py` | Implemented, returns confirmation strength, **not wired to the DB** |
| Starter table | `goalie_starts` | Exists, **0 rows** |

**Identifying the starter works.** A goalie with a `player_shifts` row at period 1 starting at 0:00 is the starter. Coverage:

| Season | Games | Both starters identified |
|---|---|---|
| 2015-16 to 2023-24 | 9,188 | 9,185 (99.97%) |
| 2024-25 | 1,312 | 1,254 (95.6%) |
| 2025-26 | 1,312 | 1,279 (97.5%) |

**Watch out: `player_shifts.start_time` has two formats.** The NHL JSON API writes `'00:00'` and the HTML fallback parser in `src/ingest/nhl_api/html_shifts.py` writes `'0:00'`. In 2025-26 the split is roughly 9,500 to 5,900 period-1 shifts. `time_to_seconds` in `src/analytics/advanced_stats/shifts.py` handles both, so the correlation engine is unaffected, but any raw string comparison silently drops a third of the season. Everything in the goalie pipeline parses times rather than comparing strings. Normalising the column on write is worth doing separately.

### Gaps to fill before modelling

1. **Shift backfill.** 58 games in 2024-25 and 32 games in 2025-26 have no shift data at all. `scripts/refetch_missing_shifts.py` exists. Without shifts we cannot tell a start from a relief appearance in those games.
2. **Goalie identities.** 143 distinct `goalie_id` values in `shot_attempts` have no row in `players`. The `players` table holds only 97 goalies (current rosters). Historical goalies need a backfill of name, team, and birthdate so career features can be joined and so the resolver can match Yahoo names.
3. **No probable-starter history.** Daily Faceoff only serves the current day. We cannot retroactively learn how often "Expected" turns into an actual start. This has to be accumulated going forward, starting with a nightly job that writes into `goalie_starts`.
4. **No materialised goalie game log.** Today `src/optimize/goalies.py` derives saves/GA/wins on the fly with per-game subqueries. That is slow and, as noted below, wrong in several places.

### External data worth adding

| Data | Why | Priority |
|---|---|---|
| Daily Faceoff probable starters, persisted nightly | The single highest-value input for P(start). Confirmation strength ("Confirmed", "Expected", "Likely") is a ready-made probability signal once calibrated | **High** |
| Goalie injury and rest news | A starter on IR has P(start) = 0 and no model feature will catch that | **High**, and `src/ingest/news/` already does this for skaters |
| Vegas moneylines and totals | Best available estimate of P(win) and game pace, and it costs one API call per slate | **Medium**, would beat a homegrown team-strength model on day one |
| Goalie birthdate and career games played | Aging curve, and career GP is the denominator for the shrinkage estimate | **Medium**, comes free with the identity backfill |

---

## Part 3: Correctness problems in the current derivation

These must be fixed in the new pipeline, not carried over from `src/optimize/goalies.py`.

1. **Shootout shots are counted as saves and goals against.** `compute_goalie_game_log` filters on `event_type` but not `period`. There were 508 period-5 shot attempts in 2024-25. NHL scoring excludes them. Fix: `period <= 4`.
2. **Wins are given to every goalie who faced a shot.** If the starter is pulled and the team comes back to win, both goalies get credit for the win. Fix: assign the decision to the goalie of record.
3. **Relief appearances are mixed in with starts.** 79 of 2,759 goalie-games in 2024-25 faced fewer than 10 shots. A model trained on "goalie appearances" learns a different, useless distribution. The conditional model must be trained on **starts only**, identified from shifts.
4. **Shutouts require playing the whole game**, not merely allowing zero goals during your appearance.
5. **Known date-leakage bug** (already flagged in `CLAUDE.md`): `compute_goalie_game_log`, `compute_crease_share`, and `compute_opponent_softness` filter by `game_id` range and never by date, so they see the full season regardless of `as_of`. Every new function takes an `as_of` date and gates on `Game.date < as_of`, matching the discipline in `src/core/queries/`.
6. **The attempt-level xG model is miscalibrated for goalie work.** Summed xG over shots-on-goal understates actual goals, and the bias drifts:

| Season | Actual goals / summed xG |
|---|---|
| 2015-16 | 1.25 |
| 2019-20 | 1.35 |
| 2023-24 | 1.41 |
| 2024-25 | 1.44 |
| 2025-26 | 1.40 |

This is expected. `src/analytics/xg/` is trained on *all* shot attempts including blocked and missed shots, so conditioning on "it reached the net" raises the true goal probability above the model's estimate. Using it raw would make every goalie's GSAx look worse over time for no real reason.

**Fix: train a separate `P(goal | shot on goal)` model** for the goalie pipeline, reusing the existing feature pipeline in `src/analytics/xg/features.py` but restricted to `event_type IN ('shot-on-goal', 'goal')`. This is standard practice and is why public sites keep separate shooter-xG and goalie-facing models. Call it `xGA` to keep it distinct.

---

## Part 4: Proposed architecture

Mirrors the skater layout, and respects the layer rule (`analytics` derives metrics, `predict` forecasts, `optimize` decides).

```
src/core/models/goalie_stats.py     GoalieGameLog table (new)

src/analytics/goalies/
    game_log.py      build GoalieGameLog from shot_attempts + shifts + games
    xga_model.py     P(goal | shot on goal), the goalie-facing xG model
    metrics.py       GSAx, danger-bucket save %, per-game and per-season

src/predict/goalies/
    constants.py     danger buckets, shrinkage constants, rolling windows
    features.py      goalie / team / opponent / context extractors
    workload.py      E[shots against]
    save_quality.py  empirical-Bayes shrunk save skill
    win_model.py     P(win)
    projections.py   combine the four components into FPTS
    starts.py        P(start)
    forecast.py      forecast_goalie() entry point
    evaluation.py    walk-forward backtest

scripts/
    build_goalie_game_log.py
    train_goalie_xga.py
    train_goalie_models.py
    ingest_goalie_starts.py   nightly Daily Faceoff persist
```

### The new table

`GoalieGameLog`, one row per goalie per game. This is the goalie analogue of `game_advanced_stats` and everything downstream reads from it instead of recomputing.

```
game_id, goalie_id, team_id, opponent_team_id, game_date
is_start          bool     first shift of period 1
is_relief         bool
toi_seconds       int      from shifts, needed for pull detection
decision          str      'W' / 'L' / 'OTL' / None
shutout           bool
shots_against     int      period <= 4, excludes shootout
saves, goals_against
xga               float    from the goalie-facing model
gsax              float    xga - goals_against
hd_sa, hd_ga, md_sa, md_ga, ld_sa, ld_ga      danger splits
sa_5v5, ga_5v5, sa_pk, ga_pk                  situation splits
empty_net_ga      int      excluded from the goalie's GA
fpts              float    computed with GOALIE_WEIGHTS
```

Danger buckets from the goalie-facing xGA per shot. Current thresholds worth starting from (2024-25 rates shown):

| Bucket | xGA per shot | Actual goal rate | Shots per goalie-season |
|---|---|---|---|
| High | >= 0.10 | 25.6% | ~176 |
| Mid | 0.04 to 0.10 | 9.5% | ~205 |
| Low | < 0.04 | 2.2% | ~335 |

Those sample sizes are the reason danger-split save percentage cannot be used as a raw feature: ~176 high-danger shots per season means a goalie's high-danger save percentage has a standard error of about 3 percentage points on a 25% base rate. It is a shrinkage input, not a predictor.

---

## Part 5: E[FPTS | start], component by component

### 5a. Shots against

The largest and most predictable term (+6.89 points per start on average).

Model: XGBoost regression, or Poisson on the count. Target is shots on goal faced in a start.

Features:

| Group | Features |
|---|---|
| Own team defence | Team SA/60 and CA/60 at 5v5 and all situations, rolling windows and season average, same disjoint-window scheme as skaters (L5 / L6-15 / L16-30) |
| Own team xGA/60 | Structure, not just volume |
| Opponent offence | Opponent SF/60, CF/60, xGF/60, HDCF/60, rolling and prior-season blended |
| Pace | Combined event rate for the two teams, whether both play a high-event style |
| Context | Home / away, own team back-to-back, opponent back-to-back, days rest for each side |
| Special teams | Own team PK time per game and opponent PP time per game, since PK shots are a meaningful share of volume |

Prior-season Bayesian blending, exactly as `extract_blended_features` does for skaters, so early-season teams are not judged on 4 games.

### 5b. Goals against

Two stages, deliberately separated:

**Stage 1: expected goals against.** Given the predicted shot volume and the opponent's shot-quality mix, what does a league-average goalie concede?

```
E[xGA] = E[shots_against] x E[xGA per shot]
```

`E[xGA per shot]` comes from the opponent's rolling danger mix (what fraction of their shots on goal are high / mid / low danger), blended with league average. This is the piece where opponent quality actually enters, and it is measurable.

**Stage 2: the goalie skill adjustment.** A multiplier on stage 1, and it should be small.

```
E[GA] = E[xGA] x save_skill_multiplier
```

`save_skill_multiplier` comes from an **empirical Bayes shrinkage estimator**, reusing the Gamma-Poisson machinery already written in `src/predict/forecasting/empirical_bayes.py`.
The exposure is career shots faced, not games.
The population prior is the league GSAx distribution.

Why empirical Bayes and not a regression: the split-half table at the top.
In-season GSAx has r = -0.05.
A regression fed rolling GSAx would happily fit noise; a shrinkage estimator with the right stabilisation constant will correctly report "this goalie is 1.00x league average" for almost everyone and only move meaningfully for goalies with thousands of career shots and a persistent gap.

Weighting: career shots faced with an exponential decay so a 34-year-old's rookie season counts less than last year, and a hard multi-season window (say 5 years) beyond which nothing counts.

Calibrate the stabilisation constant `k` empirically by maximising out-of-sample log-likelihood on held-out seasons rather than picking it by hand.

Optional refinement once the base works: shrink separately by danger bucket. Some goalies really are better on high-danger chances than their overall number suggests. The sample sizes above say this needs even harder shrinkage, so it is a phase 2 item, not phase 1.

### 5c. Win probability

Worth +1.62 points per start on average, and it is essentially a **team model**, not a goalie model.

Two options, and the second is better:

1. Build it. Team goal differential and xG differential EWMA for both sides, home ice, rest advantage, feed to logistic regression. Cheap, self-contained, probably lands around 0.58 to 0.60 accuracy.
2. Buy it. Vegas moneyline, converted to a de-vigged implied probability. This is the market's best estimate and it will beat option 1. One API call per slate.

Recommendation: build option 1 first because it needs no new data source and keeps the backtest self-contained over 11 seasons, then swap in the market line for live use if a feed is available. Structure `win_model.py` so the source is pluggable.

Careful with the goalie-of-record subtlety: P(goalie gets the win) is slightly less than P(team wins), because the goalie can be pulled. Empirically small, so start with `P(win) = P(team wins) x P(not pulled)` and estimate the pull rate from the game log.

### 5d. Shutout probability

Do not model this separately. It falls out of the GA distribution.

If GA is Poisson with mean lambda from 5b:

```
P(shutout) = exp(-lambda)
```

At lambda = 2.71 that gives 6.7%, against an actual 2024-25 rate of about 6.5% (0.15 points / 2.3). Close enough to start. NHL goals against are mildly overdispersed relative to Poisson, so if the backtest shows systematic bias, switch to a negative binomial with the dispersion parameter fit on the game log.

### 5e. Putting it together

```python
def project_goalie_start(components) -> float:
    sa     = components["shots_against"]
    ga     = components["goals_against"]
    p_win  = components["win_prob"]
    p_so   = math.exp(-ga)
    return (
        GOALIE_WEIGHTS["saves"]         * (sa - ga)
        + GOALIE_WEIGHTS["goals_against"] * ga
        + GOALIE_WEIGHTS["wins"]          * p_win
        + GOALIE_WEIGHTS["shutouts"]      * p_so
    )
```

Direct analogue of `src/predict/forecasting/projections.py::project_per_game`.

**Variance matters here too.** The sd of a goalie start is 4.10 points against a mean of 5.26, which is enormous relative to a skater game. `src/optimize/week/variance.py` already turns per-game variance into a team sigma for win probability, so the goalie forecast should return a variance alongside the mean rather than just a point estimate. Streaming two mediocre goalies and streaming one elite goalie can have the same expected value and very different distributions, and the aggression level should be able to tell them apart.

---

## Part 6: P(start)

A separate problem, with a different shape: it is a **choice among a team's goalies**, not an independent yes/no per goalie.

### Two layers

**Layer 1: confirmation (day-of truth).**
Daily Faceoff gives "Confirmed", "Expected", "Likely" with a timestamp and a source. Once persisted, each label maps to a calibrated probability learned from history. Until we have that history, sensible priors are Confirmed 0.97, Expected 0.85, Likely 0.70. `check_goalie_confirmed` already hardcodes 1.0 and 0.7; those become learned numbers.

This layer only exists for today and tomorrow. That is the actual constraint you described, and it is why layer 2 has to be good.

**Layer 2: the prior model (everything further out).**
Trained on *actual* historical starts, which we can identify perfectly from shifts back to 2015-16. No scraped labels needed, so the training set is ~22,000 team-games.

Structure: a **softmax over the team's dressed goalies for that game**, not independent binary classifiers. Conditional logit is the right family, because exactly one goalie starts and the goalies compete for the same slot. The cheap version for v1 is a binary logistic per goalie-game, then normalise within team-game so the probabilities sum to 1.

Features:

| Group | Features |
|---|---|
| Role | Rolling start share over 10, 20, and season windows (the existing `crease_share`, but date-gated) |
| Tandem structure | Number of goalies dressed, the other goalie's start share, whether the team runs a true 1A/1B |
| Rest and rotation | Days since this goalie last started, whether they started the previous team game, consecutive starts streak |
| Back-to-back | Team on a back-to-back, and **which half**. This is the single strongest schedule signal: the starter takes game 1, the backup takes game 2 |
| Schedule density | Games in the last 7 days, games in the next 7 days |
| Recent performance | GSAx over the last 3 and 5 starts. Weak as a predictor of *quality*, but coaches respond to it, so it predicts *usage* |
| Opponent | Opponent strength, home / away. Weak, include and let the model decide |
| Availability | Injury status from `player_injuries`, roster status. A hard override to 0, not a feature |

Evaluate with **log loss and Brier score**, plus a reliability curve. Calibration matters more than accuracy here because the number is multiplied straight into expected value: a systematically overconfident P(start) inflates every goalie's projection.

Baselines to beat: (a) always the goalie with the higher season start share, (b) the current `predict_starts` heuristic in `src/optimize/goalies.py`, which is "starters start everything except the second half of a back-to-back".

### How the two layers combine

```
P(start) = confirmation if available else prior_model
```

with a renormalisation across the team's goalies so the total stays at 1.
For a week-long plan, day 1 typically uses confirmations and days 2 through 7 use the prior model, and the plan is re-run daily as confirmations land.
This matters for the weekly optimiser: a goalie pickup decision made Monday for a Saturday start is a bet on the prior model, and the decision should be revisited when Saturday's confirmation appears.

---

## Part 7: Evaluation

Walk-forward, mirroring `src/predict/forecasting/evaluation.py`. Train on 2015-16 through 2022-23, calibrate on 2023-24, test on 2024-25 and 2025-26.

| Level | Metric | Beat this baseline |
|---|---|---|
| Shots against | MAE, calibration | Team season average shots against |
| Goals against | MAE, log-likelihood | League average save percentage on predicted shots |
| Win | Log loss, Brier, reliability curve | Home ice constant (~0.54) |
| FPTS given a start | MAE, correlation, calibration by decile | Goalie's own trailing FPTS per start, and league average 5.26 |
| P(start) | Log loss, Brier, reliability curve | Season start share, and the current heuristic |
| Combined weekly | MAE on realised weekly goalie FPTS | The current 60/40 blend in `evaluate_goalie_stream` |
| Decision quality | Rank correlation of projection vs actual, and top-N precision | Same |

The last row is the one that matters for the product. For streaming we do not need accurate absolute point projections, we need the right *ordering* of the ten free-agent goalies on Thursday's slate. Track Spearman correlation and "was the top-ranked available goalie in the actual top 3" alongside MAE, and be willing to accept worse MAE for better ranking.

Also worth reporting: what fraction of realised variance the model explains at all. Given the numbers in Part 1, an honest ceiling for single-game goalie FPTS R-squared is probably 0.15 to 0.25. Knowing that up front prevents chasing a number that does not exist, and it is a useful input to the aggression logic: if goalie projections are inherently low-confidence, the optimiser should weight roster stability more heavily for the G slot than it does for skaters.

---

## Part 8: Phases

**Phase 0: data foundation. Done.**
- Backfilled 143 goalie identities into `players`, which now holds 240 goalies.
- Backfilled shifts for 57 games. Starter identification went from 1,254 to 1,311 of 1,312 games in 2024-25.
- Added the `GoalieGameLog` model and migration.
- `src/analytics/goalies/game_log.py` and `scripts/build_goalie_game_log.py`, with the six correctness fixes from Part 3. Built 12,776 games, 27,334 goalie lines, 25,550 starts, 1,784 relief appearances.
- Wired into `scripts/daily_pipeline.py` as step 4.
- `scripts/ingest_goalie_starts.py` persists Daily Faceoff into `goalie_starts`. Not yet scheduled.

Two problems surfaced during the build and were fixed along the way. Both were pre-existing and neither was specific to goalies:

- **Split franchise IDs.** The NHL API reports Utah as 59 from the schedule, standings, and teams endpoints but as 68 from play-by-play and shift charts. `games` and `teams` held 59 while `game_events`, `player_shifts`, `shot_attempts`, and `game_advanced_stats` held 68, so 39 games' worth of joins silently returned nothing. Worse, `opponent_team_id` had been *derived* from the bad ID at build time, leaving a valid-looking but wrong team on 5,612 rows that no foreign key or null check would ever catch. Normalised at the ingest boundary via `TEAM_ID_ALIASES` in `src/ingest/nhl_api/client.py`, with `scripts/repair_team_ids.py` for stored rows. That script also re-derives opponent columns from the schedule, and is worth re-running after any franchise relocation or rename.
- **Unindexed shift lookups.** `player_shifts` had 9.8M rows and no index on `game_id`; the foreign key does not create one. Every per-game shift load was a sequential scan, which slowed the correlation engine and RAPM as well. Indexes added in migration `c4e1a9b7d302`, roughly a 4x speedup on the game log build.

Validation of the built log:

| Check | Result |
|---|---|
| Exactly two starts per game | 12,772 of 12,776 |
| Exactly one win and one loss per game | 12,774 of 12,776 |
| Goals against plus empty-net goals equals the opposing score (non-shootout) | 23,612 of 23,618 |
| Shutouts per season | 96 to 150, matching league history |

**Phase 2 (done ahead of Phase 1): per-start value and its variance.**

Built to satisfy `docs/plans/weekly-optimizer/04a-goalie-variance.md`, which the weekly optimizer needs before it can price a goalie at all. Lives in `src/predict/goalies/`. The four numbers the optimizer consumes come from `forecast_goalie_day`: `p_start`, `start_value`, `outcome_var`, `confidence`.

`OUTCOME_VAR = 16.87` (sd 4.11), fitted on 5,181 walk-forward starts across 2024-25 and 2025-26. One global constant, confirmed as the right shape: residual variance by projection quintile runs 16.46 to 17.51 and the affine slope is 0.070 with a bootstrap 95% CI of [-0.079, 0.212], so zero is comfortably inside. The bottom projection quartile measures 0.98x the constant, where a proportional form would have predicted 0.77x and understated it by 22%.

Calibration passes: the 80% interval covers 79.5% of outcomes, inside the required 0.75 to 0.85 band.

### Four bugs found by chasing the projection bias

The first fit came back with a -0.93 point per start over-projection, which would have inflated every goalie against every skater. Fixing it took four separate corrections, all of them era or units errors rather than modelling choices:

| Fix | Bias after |
|---|---|
| Starting point, unbounded history | -0.934 |
| Bound the lookback to three seasons, evicted in the fitter as well as the live path | -0.758 |
| Shrink save rate in **delta space** against the era it was earned in, not absolute rate | -0.609 |
| Form team factors against the **window** league level, not the current one | -0.428 |
| Symmetric win model: team goals for against **team** goals against | -0.252 |
| Shutout from **team** goals against, since an empty-netter breaks it | -0.214 |
| Explicit calibration offset for residual intra-season drift | **-0.018** |

Two of those deserve calling out because they are easy to reintroduce:

- **The double-counted era.** `SA = league_level * (team_sa / league_level) * (opp_sf / league_level)` looks era-neutral and is not, if the numerators come from a three-season window and the denominator is the current season. The window averaged 29.2 shots against a current 27.9, so each factor was inflated by about 5% and the error compounded across two of them: 3.1 shots per start, worth +0.88 points on every goalie at once. Factors must be ratios against their own era, then rescaled.
- **Goalie goals against is not team goals against.** It excludes empty-net goals, so it sits about 0.2 below what the team conceded. Feeding it into a Pythagorean whose numerator is team goals for inflated every win probability by 6 points. The same mistake overstated shutouts by a third, because a shutout is a fact about the team's scoreline, not the goalie's workload.

### The honest ceiling on the mean

The per-start model explains **0.6% of the variance** in single-start fantasy points. That is not a bug and it is not fixable: it is the brief's compressed-talent argument showing up as a number. Earlier in this document I guessed the ceiling was R-squared of 0.15 to 0.25. That was far too optimistic and is corrected here.

The practical consequence is important for how the optimizer is tuned: for goalies, essentially all of the actionable signal is in `p_start`, not in `start_value`. Picking the right goalie matters much less than knowing whether they play.

### Verification findings from the brief

Both checks the brief asked for **failed** against the pre-existing module and are now fixed:

1. **Recent form was not shrunk at all.** `compute_goalie_fpts_per_start` in `src/optimize/goalies.py` used a raw season average FPTS per start. The replacement shrinks in delta space with exposure measured in shots faced, credibility constant 1800 shots, derived by variance decomposition rather than chosen. Ten elite starts earn 0.13 weight; fifty earn 0.44, under the brief's one-half ceiling. The derivation showed that in 2015-16 the observed spread in save rate across goalies was *smaller* than binomial sampling noise, so no talent spread was detectable at all that season.
2. **Own-team offense was not an input.** The old model blended the goalie's own trailing rate with opponent softness only, so wins carried no information about whether the goalie's team could score. Own-team goals for now drives the win component directly.

### Time discipline

`goalie_starts` was an upserting table, which is the exact failure the brief warns about: a Monday decision would have read Thursday afternoon's confirmation. It is now an append-only observation log keyed by `observed_at`, read only through `src/core/queries/goalie_starts.py`, which takes an `as_of` **datetime** because confirmations land during game day and "the reports available Thursday" is not a well-formed question.

`scripts/ingest_goalie_starts.py` refuses already-played dates by default, because that page shows the settled result rather than the live report and storing it would teach the calibration that reports are far firmer than they are at decision time.

Two further leakage findings, both caught by the tests rather than by reading:

- `crease_share` had no season boundary, so a query in October read the previous season's tandem. Goalies change teams every summer, so a traded-in backup would have inherited their predecessor's share.
- The back-to-back handling used a guessed 0.35 multiplier. Measured, the right feature is not "is the team on a back-to-back" but "did *this* goalie go last night": P(start) is 0.079 in 844 measured cases if they did, and 0.397 if they were rested. That is now an explicit, date-gated check.

**Phase 1: the goalie-facing xG model.**
- `P(goal | shot on goal)`, trained on the SOG subset, reusing the existing feature pipeline.
- Validate calibration by season and confirm the 1.25 to 1.44 drift is gone.
- Backfill `xga` and `gsax` onto the game log.

**Phase 2: E[FPTS | start].**
- Shots-against model, save-quality shrinkage, win model, projection combiner.
- Backtest each component separately before combining, since a bad combined number is otherwise impossible to attribute.

**Phase 3: P(start).**
- Prior model on historical starts, then the confirmation layer once enough scraped history exists.

**Phase 4: wire into optimize.**
- Replace the 60/40 heuristic in `evaluate_goalie_stream`.
- Replace `predict_starts` with the probability model.
- Return variance alongside the mean so `src/optimize/week/variance.py` and the matchup engine can use it.
- Give goalies a proper replacement level. `src/optimize/replacement.py` currently skips them entirely, which means goalie adds are not being compared against the pool on the same footing as skater adds.

### Ordering note

Phase 0 is a hard prerequisite and is mostly plumbing.
Phase 3 (P(start)) is arguably worth more to the product than Phase 2, because the current start heuristic is crude and a wrong start prediction is a total loss while a mediocre FPTS estimate is a partial one.
Phases 2 and 3 are independent and can be built in either order once Phase 0 lands.

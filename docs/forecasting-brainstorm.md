# Forecasting System — Brainstorm & Design Notes

## Goal

A general-purpose hockey performance forecasting system that predicts
individual player stat lines for upcoming games. Driven primarily by
advanced stats from Natural Stat Trick.

Use cases:
- Predict any NHL player's near-term production (next 1-5 games)
- Identify breakout candidates / regression candidates
- Trade target evaluation
- Start/sit decisions
- General hockey analysis and projection

Fantasy hockey is a **separate overlay** on top of the core forecasting
engine — it applies league-specific scoring weights to the raw stat
predictions. The forecasting system itself knows nothing about fantasy.

### Architecture Separation

```
src/tools/forecasting/    — General-purpose stat prediction engine
src/tools/fantasy/        — Fantasy overlay (scoring weights, rankings)
                            Consumes forecasting output, adds fantasy context
```

---

## Core Prediction Targets

The system predicts **counting stats per game** for individual players:

**Primary targets:**
- Goals
- Assists
- Shots on goal
- Hits
- Blocks
- Time on ice

**Secondary targets (later):**
- Shot attempts (Corsi)
- Scoring chances

These are the raw predictions. The fantasy layer can then multiply by
scoring weights and sum to get fantasy point projections.

### Prediction Horizon

Predict each of the next 1-5 games individually. Each prediction
accounts for game-specific context (opponent, home/away, rest).

Aggregation (e.g., "projected stats over next 5 games") lives in the
consumer layer — the forecasting engine just predicts one game at a
time.

---

## Philosophy: Advanced Stats as Primary Predictors

Raw counting stats (goals, assists) are noisy game-to-game. Advanced
stats are more stable and better reflect true underlying performance.
The model should learn regression patterns (high ixG + low goals =
positive regression coming) from data rather than us hardcoding them.

### Confirmed Feature Set (v1)

See `docs/features.md` for the full table with DB column mappings.

**Individual advanced stats (per-60 rates from NST):**
- Goals/60, Assists/60, Shots/60
- ixG/60 — individual expected goals, best predictor of future
  goal scoring
- iSCF/60 — individual scoring chances, shot quality + volume
- iCF/60 — overall shot generation
- Hits/60, Blocks/60
- SH%, IPP, TOI (non-rate)

**On-ice / deployment (per-60 rates from NST):**
- CF/60 — on-ice Corsi rate
- SCF/60 — on-ice scoring chances rate
- xGF/60 — team expected goals when player is on ice
- oiSH% — on-ice shooting percentage
- OZS% — offensive zone start percentage (deployment quality)

**Context:**
- is_home (bool)
- is_b2b (bool) — player's team on back-to-back
- Opponent strength (GA/60, xGA/60) — **not yet built**, needs NHL API
- Opponent B2B — **not yet built**, needs schedule derivation

**Historical baseline (prior season per-GP rates):**
- Goals, assists, shots, hits, blocks, PIM per game
- SH%, IPP

This feature set is intentionally modifiable — we'll add/remove
features based on what actually improves predictions.

---

## Feature Window

**Default: 5 game rolling window** for advanced stat features.

Rationale: NHL situations change quickly — line changes, deployment
shifts, hot/cold streaks are real in the short term. We want to be
reactive to genuine changes without overreacting to 1-2 game blips.

Start with 5 games. Later, sweep 3-7 to validate that 5 is optimal.

Season-to-date averages and player historical baselines serve as
stabilizing anchors alongside the rolling window.

---

## Category-Specific Approaches

### Goals & Assists (the hard ones — most model sophistication)
- Primary drivers: ixG/60, iSCF/60, iCF/60 over rolling window
- Player historical baseline as anchor
- Opponent defensive quality as modifier
- Deployment (OZS%, TOI) as opportunity measure
- Where advanced stats add the most value over naive projection

### Shots on Goal
- More predictable than goals — volume is more stable than
  conversion
- Complication: shot attempts can miss or be blocked — unlucky
  streaks happen
- Features: iCF/60 (all attempts), shots/60, opponent block rate
- Moderate model sophistication

### Blocks
- Hypothesis to validate: does opponent shot volume correlate with
  individual blocks?
- Combine opponent shot volume with player's own blocking rate
  (blocks/60 over rolling window)
- Simpler model — less need for advanced stats

### Hits
- Largely player-dependent, not very matchup-sensitive
- Rolling average of hits/60 over rolling window
- Maybe minor opponent adjustment
- Simplest model of the group

### Time on Ice
- Relatively stable for established players
- Affected by game situation (blowouts, OT), injuries to
  teammates, coach decisions
- Rolling average with some game-context adjustment

---

## Player-Level Adjustment Factor

Each player gets a historical baseline that anchors predictions.
Career/prior-season rates serve as a prior:

- Early in the season (small sample): lean more on the prior
- As the season progresses: lean more on current-season data

This is Bayesian shrinkage in spirit — blend current performance
toward a prior based on sample size. The model should learn how much
to weight the prior vs. recent data.

---

## Opponent Adjustment

For a player facing Team X, we need team-level defensive quality:
- Team X goals against per 60 (relative to league average)
- Team X xGA/60
- Team X shots against per 60
- Team X penalty kill effectiveness / penalty frequency

**Data source: NHL API** for team-level stats (points, GA, etc.).
Can query team stats at any point in the season.

### Power Play Factor (future enhancement)

Teams that commit more penalties or have weaker penalty kills create
more PP opportunity. This matters for PP-heavy players. To be
discussed further — involves modeling PP time separately from 5v5.

---

## Model Architecture

**Starting approach: Gradient-Boosted Trees (XGBoost / LightGBM)**

- Industry standard for tabular data like this
- Learns nonlinear relationships (e.g., ixG regression patterns)
- Feature importance is interpretable — we can see what drives
  predictions
- Fast to train and iterate
- Works well with mixed feature types (rates, bools, counts)

The existing heuristic baselines (season average, weighted blend)
serve as the bar to beat. If gradient-boosted trees can't beat a
simple season average, something is wrong.

Model architecture is an area for ongoing discussion — we may revisit
after seeing initial results.

---

## Evaluation Strategy

### What We Evaluate

Evaluate on **predicted counting stats vs. actual counting stats**.
Not fantasy points — the forecasting engine doesn't know about fantasy.

### Per-Game Evaluation
- Compare predicted stat line to actual for each game
- Useful but inherently noisy (a player either scores 0 or 1+ goals
  in a game — hard to evaluate a 0.3 goals prediction on one game)
- Worth tracking but not the primary metric

### 5-Game Window Evaluation (primary)
- Sum model predictions over 5 consecutive games
- Compare to actual 5-game totals
- Smooths single-game noise, more meaningful signal
- This is the most practically useful evaluation unit

### Error Function
- TBD — to be determined through experimentation
- Candidates: MAE, Poisson log-likelihood (good for count data),
  RMSE, distribution-based metrics
- Want to evaluate both accuracy and calibration (are the
  predictions well-distributed, not just clustered around the mean?)

### Walk-Forward Backtest

This is the methodology for testing a model on historical data without
cheating. In plain terms:

1. Pick a historical season (e.g., 2023-24)
2. Go through the season chronologically, game by game
3. For each game: the model can ONLY see data from BEFORE that game
   (no peeking at future results)
4. Model makes its prediction, we record what actually happened
5. Move to the next game, repeat

This simulates real-world usage — the model never sees the future.
It's the gold standard for evaluating time-series predictions. Already
implemented in `BacktestHarness`.

---

## Training Data

**Plan: ~3 seasons of historical data (2022-2025)**

Scrape individual game logs from NST for these seasons. This gives:
- ~3,700 games (~1,230/season * 3 seasons)
- ~500+ unique players per season
- Hundreds of thousands of player-game observations

On-ice game logs can be added later (doubles scraping load).

### Situations

**Combined ("all situations") for now.** Separating 5v5 and power
play is a future enhancement — adds complexity and requires modeling
PP opportunity separately.

---

## Decided

- [x] General-purpose forecasting, fantasy as separate overlay
- [x] Predict counting stats per game (goals, assists, shots, hits,
      blocks, TOI)
- [x] Advanced stats as primary features (ixG/60, iSCF/60, etc.)
- [x] ~5 game rolling window (test 4-10)
- [x] Gradient-boosted trees as starting model
- [x] Player historical baseline as prior
- [x] Opponent adjustment via NHL API team stats
- [x] Walk-forward backtest for evaluation
- [x] ~3 seasons training data (2022-2025)
- [x] All situations combined for v1

## To Be Decided Later

- [ ] Exact error function / loss metric
- [ ] Fantasy scoring weights (for fantasy overlay)
- [ ] Power play modeling (separate from 5v5?)
- [ ] Goalie forecasting (completely different problem)
- [ ] Specific features to add/remove after initial results

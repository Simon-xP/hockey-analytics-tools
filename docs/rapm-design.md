# RAPM Player Rating & Teammate Elevation Model

## 1. Problem Statement

PuckAgent needs to answer: **how much does a player elevate their teammates?**

This is distinct from "how good is a player individually." A player who generates lots of shots personally raises on-ice metrics but doesn't necessarily make linemates better. A true elevator creates space, drives possession transitions, and enables linemates to produce more than they would otherwise.

For fantasy hockey, this matters because **offensive elevation translates directly to fantasy value**. If player A makes their linemates score more goals and generate more shots, those linemates are undervalued by raw stats alone — and A's presence is the hidden driver. Defensive impact matters indirectly through coach trust and deployment decisions (a player who suppresses chances earns more ice time), but fantasy scoring (G=3, A=2, SOG=0.3, HIT=0.4, BLK=0.5) overwhelmingly rewards offensive production.

Concretely, we need:

1. **Player ratings** — an independent measure of each player's contribution to on-ice offensive production, controlling for who they play with and against.
2. **Linemate quality scores** — for any player, the average offensive rating of their linemates weighted by shared ice time. This directly fills the "biggest infrastructure gap" identified in `docs/upside-and-opportunity.md`.
3. **Elevation metric** — how much a player's presence improves teammates' offensive production beyond what their ratings predict. This goes beyond additive RAPM to capture synergistic, play-driving effects.

The first two are solved by RAPM (Regularized Adjusted Plus-Minus). The third requires WOWY (With Or Without You) analysis built on top of RAPM.

## 2. Background: RAPM in Hockey Analytics

RAPM is the standard method for isolating individual player contributions from team effects.

### How it works

Consider a single 40-second shift segment where skaters A, B, C, D, E are on ice for the home team, and F, G, H, I, J are on ice for the away team. During that segment, the home team generated 0.15 xG. That's an xGF/60 rate of (0.15 / 40) × 3600 ≈ 13.5.

RAPM models that rate as the sum of each skater's individual contribution:

```
13.5 = β_A + β_B + β_C + β_D + β_E − β_F − β_G − β_H − β_I − β_J + home_ice + score_effect
```

One equation, 10 unknowns — unsolvable alone. But across ~1 million segments, you get ~1 million equations. Players appear in different combinations: A plays with B sometimes, with K other times; F faces different opponents every game. The more a player appears in varied linemate/opponent contexts, the more the system can isolate their individual contribution.

The problem is that linemates are correlated — first-line players often play together. Without regularization, the regression assigns wildly large positive and negative coefficients to always-together linemates (since it can't tell who's responsible). **Ridge regularization** (L2 penalty) adds a cost for extreme coefficients, which pulls uncertain estimates toward zero (league average) and only lets players with strong, consistent signals across many different contexts develop large coefficients.

The coefficients β are the player ratings: each one represents how much that skater independently adds to (or subtracts from) the on-ice xGF/60 rate, controlling for every other skater on the ice.

Whatever's left over — the residual between actual xGF/60 and the sum of individual coefficients — represents combination effects, noise, coaching systems, and non-additive chemistry. The WOWY elevation metric (Section 5.2) tries to measure whether that residual is systematically positive for a specific player's linemates.

Public implementations: Evolving Hockey's GAR/WAR, HockeyViz, TopDownHockey. All use variants of this approach with xG or goal-based outcomes.

## 3. Model Specification

### 3.1 Shift Segments

A **shift segment** is a maximal time interval within a period where the on-ice personnel do not change. Every shift start or end by any skater (either team) creates a segment boundary.

Construction:
1. For each game period, collect all shift start and end times across both teams.
2. Sort and deduplicate these timestamps to get breakpoints.
3. Each pair of consecutive breakpoints defines a segment.
4. For each segment, look up which players have an active shift spanning that interval.

Filter segments to:
- **5v5 only** (situation code indicates 5 skaters + 1 goalie per side). PP/PK have fundamentally different dynamics, smaller samples, and confound the elevation signal.
- **Duration >= 2 seconds**. Sub-second segments from near-simultaneous changes are noise.
- **Regular time only** (periods 1-3). Overtime is 3v3 with different tactical dynamics.

**Existing infrastructure:** `src/analytics/advanced_stats/correlate.py` contains the core building blocks for shift-event correlation. Two functions are already public and can be imported directly:
- `time_to_seconds()` — MM:SS to seconds conversion
- `classify_situation()` — situation code → "5v5", "pp", "pk", etc.

The remaining helpers (`_load_shifts`, `_load_shot_xg`, `_build_situation_timeline`, `_players_on_ice`) are underscore-prefixed private functions. Rather than importing private internals, we **refactor**: extract the shared shift utilities into `src/analytics/advanced_stats/shifts.py` as public functions, then have both `correlate.py` and the RAPM module import from there. This is a prerequisite step (see Phase 0 in Implementation Plan).

The new RAPM work is organizing these into **segment-first** construction. `correlate.py` is event-centric — it processes events and looks up who's on ice for each one. We need the inverse: enumerate all continuous personnel intervals and then attribute events to them.

### 3.2 Response Variables

We run **two separate one-sided models**:

| Model | Response | Design matrix encodes | Purpose |
|-------|----------|----------------------|---------|
| **Offensive** | xGF/60 | Only the "for" team's skaters (+1) | How much does this player contribute to their own team's shot generation? |
| **Defensive** | xGA/60 | Only the "against" team's skaters (+1) | How much does this player suppress the opponent's shot generation? (lower = better) |

**Why one-sided encoding matters:**

A naive two-sided encoding (+1 for home, -1 for away) conflates offense and defense in each coefficient. When player F is on the away side and the home team's xGF/60 drops, is that because F is a great defender suppressing the home team, or because F is a poor offensive player who doesn't generate xG when his team has the puck? The two-sided model can't tell — β_F blends both signals.

One-sided encoding fixes this. Each shift segment produces **two training rows**:

1. **Offensive row:** response = xGF/60 for team X. Only team X's 5 skaters get +1 indicators. Team Y's skaters are all 0.
2. **Offensive row:** response = xGF/60 for team Y. Only team Y's 5 skaters get +1. Team X's skaters are all 0.

Same structure for the defensive model but with xGA/60. Now each coefficient purely measures one side: the offensive coefficient captures "how much does this player contribute to their own team's xG generation," with no defensive signal mixed in.

**Priority:** The offensive model is the primary output for fantasy. The defensive model is built on the same infrastructure and is cheap to run alongside, but is secondary — useful for future analysis (e.g., blending 90% offensive + 10% defensive for a composite rating, identifying sheltered players getting carried by defensive linemates, understanding coach trust).

**xG source:** `shot_attempts.xg` — our trained xG model's predictions (0.83 AUC), joined to shift segments by game_id, period, and time range.

Why xG over goals:
- Goals are binary and rare (~0.08 per shot). A single lucky/unlucky goal in a 45-second segment dominates the signal.
- xG is continuous and accumulates with every shot attempt, giving ~10x more information per minute of ice time.
- xG is the foundation of our existing analytics pipeline.

### 3.3 Design Matrix

Each shift segment produces **2 rows** (one per team). For N shift segments and P unique skaters:

```
X: 2N × (P + covariates) sparse matrix
y: 2N × 1 response vector (xGF/60 for that team)
w: 2N × 1 weight vector (segment TOI in minutes)
```

**Player columns (P):**
- +1 if skater is on the "for" team in this row
- 0 otherwise (including all opposing skaters)

Each row only has 5 non-zero player entries (one team's skaters). This doubles the row count compared to two-sided encoding, but the matrix is sparser (5 non-zeros per row vs 10), so memory and compute are comparable.

Goalies are excluded — they're always on ice for their team during 5v5 play, so they'd be collinear with the intercept. Goalie influence on xGF is minimal (xG measures pre-shot quality, not save probability).

**Covariate columns:**
- `is_home`: +1 if the "for" team is the home team, 0 if away (captures home-ice advantage in shot generation)

Score state is handled by **filtering** (Section 3.5), not as a covariate.

**Not included as covariates:**
- Zone starts: deliberately excluded. Zone start allocation is a coaching deployment decision — including it as a covariate would partial out exactly the deployment signal we want to measure. A player who gets more offensive zone starts IS in a better deployment, and we want the model to reflect that.
- Time in game: minimal effect in modern NHL. Could revisit if validation shows period effects.

### 3.4 Regularization

**Ridge regression** (L2 penalty): minimize `Σ w_i (y_i - X_i β)² + λ Σ β_j²`

The penalty applies only to player coefficients, not the `is_home` covariate. The covariate is estimated unpenalized.

**Tuning λ:**
- 5-fold cross-validation on held-out shift segments, minimizing weighted MSE.
- Folds are stratified by game (all segments from a game go in the same fold) to avoid leaking within-game correlation.
- Search over logarithmic grid: λ ∈ {0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100}.

**Expected behavior:** Higher λ shrinks all player ratings toward zero (league average). Players with more TOI are less affected by shrinkage because they contribute more observations. This naturally handles sample-size — a 4th-liner with 400 minutes gets a more-shrunk rating than a 1st-liner with 1200 minutes, which is appropriate.

### 3.5 Score-State Filtering

Filter to segments where the score differential is **within 2 goals** (|diff| <= 2).

From our data (2022-23 through 2025-26, 5v5 shot attempts only):

| Score differential | % of 5v5 shots | Cumulative |
|--------------------|----------------|------------|
| Tied (0) | 35.5% | 35.5% |
| ±1 goal | 35.5% | 71.0% |
| ±2 goals | 16.5% | **87.5%** |
| ±3+ goals | 12.5% | 100% |

Within-2 keeps 87.5% of data while removing the most distorted game states — at 3+ goal differentials, trailing teams take aggressive risks and leading teams play passive, fundamentally warping the shot generation patterns we're trying to measure.

This is preferred over a covariate approach because:
- Score effects at 3+ goals are qualitatively different (garbage time), not just a linear shift that a coefficient can capture.
- Only 12.5% data loss — negligible impact on sample size.
- Simpler model with fewer parameters.
- No need to decide how to bin score states or whether the relationship is linear.

### 3.6 Weighting

Each shift segment is weighted by its duration in minutes. Longer segments provide more reliable rate estimates and more opportunities for events to occur.

This is equivalent to modeling total xG counts with a TOI offset, but the rate formulation is more interpretable (coefficients are in xG/60 units).

## 4. Data Pipeline

### 4.1 Shift Segment Construction

**Input:** `player_shifts` table + `game_events` (for situation codes) + `shot_attempts` (for xG attribution)

**Reused from `src/analytics/advanced_stats/shifts.py`** (extracted in Phase 0):
- `time_to_seconds()`, `classify_situation()`, `build_situation_timeline()` — shared utilities
- Shift loading and indexing pattern — adapted from `load_shifts()` and the shift_index construction in `compute_game_advanced_stats()`
- `players_on_ice()` — used to determine on-ice sets per segment

**New logic:** Segment boundary construction and xG aggregation per segment.

**Algorithm:**

```
for each game:
  load shifts, situation_timeline, shot_xg (reuse correlate.py helpers)
  determine home_team_id, away_team_id from games table
  build shift_index (same pattern as correlate.py)
  initialize running_score = 0

  for each period (1, 2, 3):
    1. Collect all shift start_time and end_time for both teams → parse to seconds
    2. Collect all unique timestamps → sorted breakpoints
    3. For each consecutive pair (t_start, t_end) where t_end > t_start:
       a. Look up situation from situation_timeline at segment midpoint
       b. Classify via classify_situation() — if not "5v5", skip
       c. Find on-ice players for each team via _players_on_ice()
       d. Separate skaters from goalies (goalies excluded from design matrix)
       e. Verify exactly 5 skaters per team — if not, skip (data quality issue)
       f. Sum xGF (home team shots' xG) and xGA (away team shots' xG)
          from shot_attempts falling in [t_start, t_end)
       g. Record segment with all metadata
    4. Update running_score from any goals in this period
```

### 4.2 Data Volume & Coverage

**Available data:** 11 NHL seasons of shift + xG data:

| Season | Games | Shifts | Shot Attempts (xG scored) |
|--------|-------|--------|---------------------------|
| 2015-16 | 1,148 | ~710K | 128,007 |
| 2016-17 | 1,148 | ~710K | 129,483 |
| 2017-18 | 1,189 | ~750K | 140,605 |
| 2018-19 | 1,189 | ~750K | 137,254 |
| 2019-20 | 1,012 | ~620K | 115,240 (COVID-shortened) |
| 2020-21 | 812 | ~490K | 88,241 (56-game season) |
| 2021-22 | 1,230 | ~780K | 140,343 |
| 2022-23 | 1,230 | ~780K | 144,114 |
| 2023-24 | 1,230 | ~780K | 150,333 |
| 2024-25 | 1,254 | ~946K | 156,512 |
| 2025-26 | 1,280 | ~961K | 150,198 |

With ~400 breakpoints per game (measured from 2024-25 data), we get approximately **150-250 5v5 segments per game** (not all breakpoints yield 5v5 segments — some fall during PP/PK).

**Recommended training window: 4 seasons (2022-23 through 2025-26).**

Rationale:
- ~4,994 games → **~750K-1.2M 5v5 segments.** More than sufficient.
- ~1,100 skaters above the 200-minute 5v5 TOI threshold (verified from `game_advanced_stats` for 2022-25; adding 2025-26 only increases this).
- Modern enough: NHL play style, rules, and officiating have evolved. Pre-2020 data reflects a meaningfully different game (different icing rules, COVID protocol effects on 2020-21, etc.).
- 4 seasons provides enough overlap for players who changed teams, so RAPM can properly separate individual from team effects via the "natural experiments" of trades and free agency.

**Why not all 11 seasons?** Older data adds players who are retired and irrelevant, without improving estimates for current players (a 2015-16 4th-liner who retired in 2019 contributes nothing to rating current players). It also increases computation time and design matrix size for diminishing returns. The 200-min TOI filter would exclude most of those old players anyway, but their teammates' segments still contribute noise.

**Why not fewer seasons?** With only 1-2 seasons, RAPM struggles to separate linemates who always play together. More seasons = more roster turnover = better identification. 4 seasons hits the sweet spot.

**Design matrix dimensions:** ~1M rows × ~1,500 columns (skaters + covariates). Sparse — each row has exactly 10 non-zero player entries. `scipy.sparse` CSC format, ridge regression via `sklearn.Ridge` or `scipy.sparse.linalg` — fits in seconds.

**Minimum TOI filter:** Exclude skaters with < 400 minutes of 5v5 TOI across the 4-season window. This is roughly a full season for a regular player, providing enough observations for ridge regression to produce a meaningful estimate rather than just shrinking to zero.

### 4.3 Storage

**Shift segments** are expensive to rebuild (~minutes per season), so we persist them.

New table: `shift_segments`

| Column | Type | Description |
|--------|------|-------------|
| id | integer PK | Auto-increment |
| game_id | integer FK | References games.game_id |
| period | smallint | 1, 2, 3 |
| start_seconds | smallint | Segment start (elapsed in period) |
| end_seconds | smallint | Segment end |
| duration_seconds | smallint | end - start |
| situation | varchar(8) | "5v5", "4v4", etc. |
| score_state | smallint | Home lead at segment start (for filtering to within-2) |
| home_skater_ids | integer[] | Sorted array of 5 home skater NHL IDs |
| away_skater_ids | integer[] | Sorted array of 5 away skater NHL IDs |
| home_xgf | float | Sum of home team xG during segment |
| away_xgf | float | Sum of away team xG during segment |

Indices: `(game_id, period)`, `situation`.

We store all situations (not just 5v5) so the table is a general-purpose resource. RAPM queries filter to `situation = '5v5'`.

**RAPM results** — player ratings per model run:

New table: `player_ratings`

| Column | Type | Description |
|--------|------|-------------|
| id | integer PK | Auto-increment |
| player_id | integer | NHL player ID |
| model_version | varchar(32) | e.g. "rapm_v1_5v5" |
| seasons | varchar(64) | e.g. "20222023,20232024,20242025,20252026" |
| rating_off | float | Offensive model coefficient (xGF/60 contribution) |
| rating_def | float | Defensive model coefficient (xGA/60 suppression; lower = better) |
| toi_minutes | float | Total 5v5 TOI in dataset |
| percentile_off | smallint | 1-100 offensive percentile among qualifiers |
| percentile_def | smallint | 1-100 defensive percentile among qualifiers |
| computed_at | timestamp | When the model was run |

Unique constraint: `(player_id, model_version, seasons)`.

## 5. From RAPM to Elevation

RAPM gives us offensive player ratings. Two derived metrics serve the fantasy use case:

### 5.1 Linemate Quality (deployment quality metric)

For each player on a per-game or rolling basis:

```
linemate_quality = Σ (shared_toi_with_teammate × teammate_off_rating) / total_5v5_toi
```

Summed over all teammates who shared ice time during the evaluation window (e.g., last 5 games). This tells you: on average, how offensively productive are the players this person is deployed with?

**Use case:** A free agent whose linemate quality jumps from the 30th to the 70th percentile has been promoted to play with better offensive players. This is a strong opportunity signal — even if the player's own talent hasn't changed, they're now getting passes from and making plays with more dangerous teammates. That should translate to more goals, assists, and shots.

**Computation:** Query `shift_segments` for the player's recent games, look up on-ice teammates, cross-reference with `player_ratings`. No new table needed — this is a real-time calculation from existing persisted data.

### 5.2 Teammate Elevation (WOWY-based)

RAPM assumes additive, independent player effects. But some players have **synergistic** effects — they make specific linemates (or linemates in general) produce more than the sum of parts. This is the core question: **who makes their teammates better offensively?**

**Method: WOWY residual analysis**

For each player A and each teammate B who shared significant ice time:

1. **With A:** Compute the on-ice xGF/60 during shift segments where both A and B are on ice.
2. **Without A:** Compute the on-ice xGF/60 during segments where B is on ice but A is not.
3. **RAPM-predicted difference:** Sum the offensive RAPM coefficients of all "for" team skaters in the "with A" segments and compare to the "without A" segments. This predicts how much of the xGF/60 gap is explained by replacing A (and whoever else changed) with whoever actually replaced them.
4. **Elevation residual:** Actual (with - without) gap minus the RAPM-predicted gap.

If B consistently produces more xGF/60 with A on ice than RAPM predicts from the personnel alone, A is elevating B offensively.

**Aggregate elevation score:**

```
elevation(A) = weighted_mean over all teammates B of elevation_residual(A, B)
               weighted by shared TOI with B
```

A positive aggregate means A makes linemates more offensively productive than RAPM's additive model predicts. This captures:
- **Playmaking:** A creates high-quality chances for linemates (their shots are from better locations when A is on ice)
- **Transition play:** A drives the puck into the offensive zone, giving linemates more time and space
- **Puck retrieval:** A wins board battles and sustains offensive zone time
- **Decoy effect:** A draws defensive attention, opening up linemates

These are all things that create fantasy value for teammates but may not show up in A's personal shot metrics.

**Fantasy application:** When a player with high elevation gets a new linemate (via trade, injury replacement, or line shuffle), that new linemate's fantasy value is likely to increase more than their baseline stats suggest. Conversely, when an elevator gets injured, their linemates' production drops more than expected.

**Minimum thresholds:**
- Each A-B pair requires >= 100 minutes together AND >= 100 minutes of B without A, both at 5v5. Below this, the WOWY comparison is too noisy.
- Player A needs >= 3 qualifying teammate pairs to compute a stable aggregate elevation score.

**Position-aware considerations:**

Defensemen and forwards have structurally different deployment patterns that affect WOWY analysis:

- **Defensemen** play in pairs. Pairings are more stable than forward lines — a top-4 D might have only 3-6 partners who clear the 100-min shared TOI threshold across 4 seasons (primary partner at ~1,000 min/season, secondary at ~300, injury fill-ins at ~100-200). The 3-pair minimum is the binding constraint for D — most will qualify, but it's tight.
- **Forwards** play in lines of 3 and lines get shuffled more frequently. A top-6 forward typically has 6-10+ qualifying teammates over 4 seasons. The 3-pair minimum is easily met.

When computing linemate quality (Section 5.1), we should weight by shared TOI regardless of position. But when interpreting elevation scores, D elevation is based on fewer, higher-TOI comparisons (fewer pairs, each with more minutes) while forward elevation is based on more, lower-TOI comparisons. Both are valid but have different noise profiles.

**Limitations:**
- WOWY is confounded by deployment context — when A is out, B might face different opponents or game states. RAPM partially controls for this (we subtract the RAPM-predicted gap based on actual replacement personnel), but not perfectly.
- Elevation scores are noisier than raw RAPM ratings because they depend on the difference-of-differences.
- Some "elevation" is really "coach puts A and B together because they have chemistry" rather than A causing B's improvement. We can't fully distinguish cause from correlation. But for fantasy purposes, this distinction doesn't matter — if B produces more with A regardless of why, that's actionable.

## 6. Validation

### 6.1 Stability (Split-Half Reliability)

**Test:** Split each season's games into odd and even games. Run RAPM independently on each half. Compute Pearson correlation of offensive ratings between halves (filtered to players with >= 400 min in both halves).

**Expectations:**
- Offensive rating correlation: 0.4-0.6 (consistent with published RAPM literature).
- Minimum TOI filter of 400 min should yield stable estimates.

**Failure criterion:** If split-half r < 0.3, the model is too noisy to be useful. Investigate: increase regularization, increase minimum TOI, or pool more seasons.

### 6.2 Predictive Power

**Test:** Train RAPM on seasons N-1 and N. Predict season N+1 on-ice xGF/60 for each player.

Specifically: for each skater with >= 400 min in both training and test:
1. Predict their 5v5 on-ice xGF/60 in season N+1 using their RAPM offensive rating from training.
2. Compare prediction to actual (weighted by TOI).
3. Benchmark against naive baselines: (a) player's raw on-ice xGF/60 from training, (b) league average (zero prediction).

**Expectations:** RAPM should beat raw rates (which are confounded by linemates) and should beat league average. Published results show ~0.3-0.4 year-over-year correlation for RAPM-based ratings.

### 6.3 Sanity Checks

Spot-check that known elite offensive drivers (McDavid, MacKinnon, Kucherov, Matthews) rate in the top tier. Players known for their play-driving ability but who aren't pure goal scorers (Huberdeau, Kadri) should also show meaningful positive ratings. If a known quantity is way off, investigate before trusting the model.

### 6.4 Elevation Metric Validation

**Test:** Players with high elevation scores should predict future linemate offensive production improvements.

1. Identify players with top-20% elevation scores in season N.
2. When those players get new linemates in season N+1 (via trade, line shuffle), do the new linemates' offensive production increase more than expected?
3. Compare to a control group: linemate changes involving low-elevation players.

This is the hardest validation because it requires sufficient linemate-change events and is inherently noisy. Treat as exploratory rather than pass/fail.

**Additional angle:** correlate elevation scores with primary assist rates. Players who make linemates better offensively should tend to have high A1/A ratios (their assists come from directly setting up goals, not secondary touches). This isn't proof of elevation, but a strong correlation would increase confidence.

## 7. Integration with PuckAgent

### 7.1 Opportunity Model Features

RAPM outputs feed the opportunity model as candidate features for the feature discovery pipeline (`docs/upside-and-opportunity.md`):

| Feature | Description | Fantasy hypothesis |
|---------|-------------|--------------------|
| `linemate_quality_5g` | Avg offensive RAPM of linemates, last 5 games | Higher-rated linemates → more assists, more shots from better passing, more goals from better setups |
| `linemate_quality_delta` | 5-game linemate quality minus 20-game average | Sudden jump = recent promotion to a better line → fantasy breakout |
| `own_rating_pctile` | Player's offensive RAPM percentile | Context: controls for player talent in opportunity model |
| `deployment_gap` | linemate_quality - own_rating | Overdeployed (gap > 0) = getting a boost from context; underdeployed (gap < 0) = talent being wasted |
| `elevator_nearby` | Max elevation score among current linemates | Playing with an elevator amplifies your own production |

The `linemate_quality_delta` is likely the most actionable feature — a sudden jump means the player was recently promoted to better linemates, which is the core "opportunity" signal.

### 7.2 Upside Model Feature

Elevation score itself is a candidate **upside** feature for the player being evaluated:

| Feature | Description | Fantasy hypothesis |
|---------|-------------|--------------------|
| `elevation_score` | Player's aggregate WOWY elevation | High elevators sustain linemate production → coach keeps them deployed → stable fantasy floor |

A player with high elevation is less likely to lose their deployment than a player who happens to be on a hot line but isn't driving the results. This is a durability signal.

### 7.3 Player Value Integration

`PlayerValue` in `src/optimize/models/` already has `opportunity_score`. RAPM-derived features become inputs to computing that score, eventually replacing (or augmenting) the current hand-tuned TOI-trend and deployment-share components in `src/predict/signals/opportunity.py`.

### 7.4 Update Cadence

- **Shift segments:** Computed incrementally as new games are ingested (part of daily pipeline). `correlate.py` already runs per-game; segment building piggybacks on the same data load.
- **RAPM model:** Re-fit weekly or on-demand. Ridge regression on ~1M sparse rows takes seconds on modern hardware. Rolling 4-season window: drop oldest season at start of each new NHL season.
- **Linemate quality:** Computed on-the-fly from shift segments + cached player ratings. Cheap enough to not require caching.
- **Elevation scores:** Recomputed monthly or on-demand. More expensive (pairwise WOWY comparisons) but not time-critical.

## 8. Implementation Plan

### Phase 0: Extract Shared Shift Utilities

**Module:** `src/analytics/advanced_stats/shifts.py`

Extract the reusable shift helpers from `correlate.py` into a shared public module:
- `time_to_seconds()` and `classify_situation()` — already public, just move
- `load_shifts()` — load shift records for a game
- `load_events()` — load event records for a game
- `load_shot_xg()` — load xG predictions keyed by (game_id, event_id)
- `build_situation_timeline()` — per-period timeline of situation code changes
- `players_on_ice()` — binary-search lookup of who's on ice at a given time

Update `correlate.py` to import from `shifts.py` instead of defining these inline. Verify existing tests still pass.

**Tests:** Run existing test suite to confirm refactor is behavior-preserving.

### Phase 1: Shift Segment Builder

**Module:** `src/analytics/rapm/segments.py`

Build the shift segment table from `player_shifts`, `game_events`, and `shot_attempts`.

Imports from `src/analytics/advanced_stats/shifts.py`: `time_to_seconds`, `classify_situation`, `build_situation_timeline`, `players_on_ice`, `load_shifts`, `load_shot_xg`, `load_events`.

New code:
- Breakpoint enumeration from shift boundaries per game-period
- Segment construction between consecutive breakpoints
- Situation determination per segment (reusing situation timeline)
- Score state tracking from goal events
- xG aggregation per segment from `shot_attempts`
- Batch persistence to `shift_segments` table
- Incremental processing: only build segments for games not yet in the table

**Tests:**
- Unit test segment construction with a hand-crafted shift scenario (known breakpoints, known on-ice sets)
- Verify segment durations sum to period length (minus stoppages and non-5v5 time)
- Verify xG attribution: sum of segment xGF/xGA matches `game_advanced_stats` totals per game
- Test edge cases: overtime periods excluded, very short segments filtered, simultaneous line changes

**Alembic migration** for `shift_segments` table.

### Phase 2: RAPM Model

**Module:** `src/analytics/rapm/model.py`

One-sided ridge regression on shift segments to produce player ratings. Runs two models on the same segments:

1. **Offensive model:** Each segment → 2 rows (one per team). Response = that team's xGF/60. Only that team's 5 skaters get +1 indicators. Coefficients = each player's contribution to their own team's offensive generation.
2. **Defensive model:** Same structure but response = xGA/60 (what the opponent generated against this team). Coefficients = each player's contribution to suppressing opponent chances (lower = better defensively).

Both models share the same design matrix structure and λ tuning pipeline.

Implementation:
- Build one-sided sparse design matrix from `shift_segments` — each segment produces 2 rows (home team perspective + away team perspective), each with only 5 non-zero player entries
- Map player IDs to column indices; build reverse mapping for result extraction
- Fit weighted ridge regression (`sklearn.linear_model.Ridge` with `sample_weight`)
- Cross-validate λ with game-stratified folds (same folds for both models)
- Extract coefficients, compute percentiles for offensive and defensive separately
- Store both ratings in `player_ratings` table

**Tests:**
- Synthetic test: create fake segments where player A always adds +0.5 xGF/60. Verify offensive RAPM recovers A's rating close to +0.5 and defensive model is unaffected (A's defense coefficient should be near zero).
- Verify one-sided separation: a player who is purely a defensive presence (suppresses opponent xG but doesn't generate offense) should have a strong defensive rating but near-zero offensive rating.
- Verify regularization: with very high λ, all ratings should be near zero. With λ=0, ratings should be noisy/extreme.
- Verify home-ice coefficient is positive (well-established prior).
- Verify rating distribution is centered near zero (by construction).
- Integration test: run on one real season, spot-check that McDavid / MacKinnon are in top 20.

**Alembic migration** for `player_ratings` table.

### Phase 3: WOWY Elevation Metric

**Module:** `src/analytics/rapm/elevation.py`

WOWY residual analysis built on shift segments + RAPM ratings.

- For each player A, find all teammates B with sufficient shared TOI
- Compute "with A" and "without A" xGF/60 for each B
- Compute RAPM-predicted gap from personnel differences
- Residual = actual gap minus predicted gap
- Aggregate across all qualifying teammates, weighted by shared TOI

**Tests:**
- Synthetic test: inject a known elevation effect (player A adds +0.3 xGF/60 beyond what RAPM predicts for any set of linemates) and verify the elevation metric recovers it approximately.
- Minimum threshold enforcement: player pairs below 100 min together are excluded, players with < 3 qualifying pairs return None.
- Sign check: known elite playmakers (McDavid, Draisaitl) should have positive elevation.

### Phase 4: Linemate Quality Metric

**Module:** `src/analytics/rapm/metrics.py`

Linemate quality and derived features computed from segments + ratings.

- `compute_linemate_quality(session, player_id, as_of, window_games=5)` → float
- `compute_linemate_quality_delta(session, player_id, as_of)` → float (5-game minus 20-game)
- Percentile conversion utilities

**Tests:**
- Linemate quality: if player A always plays with top-rated players, their linemate quality should be high.
- Delta: simulate a player who switches from bad to good linemates mid-window; verify delta is positive.
- Temporal correctness: uses only games before `as_of` date (no leakage).

### Phase 5: Validation Suite

**Module:** `src/analytics/rapm/validation.py`
**Script:** `scripts/validate_rapm.py`

Run all validation checks from Section 6 and produce a report.

- Split-half reliability
- Year-over-year prediction vs baselines
- Spot-check known players (table output)
- Elevation correlation with primary assist ratio
- Output: printed summary + JSON for programmatic consumption

### Phase 6: Opportunity Model Integration

Wire RAPM-derived features into the opportunity model in `src/predict/signals/opportunity.py`.

- Add `linemate_quality` and `linemate_quality_delta` as opportunity components
- Add `elevation_score` as an upside component candidate
- These join the existing TOI-trend and deployment-share components
- Weights remain hand-tuned initially; the feature discovery harness (documented in `docs/upside-and-opportunity.md`) will eventually learn optimal weights empirically

### Phase 7: Daily Pipeline Integration

Add shift segment computation to `scripts/daily_pipeline.py` so segments are built incrementally.

- After `ingest_game_events` and `score_xg`, run segment builder for new games
- Weekly RAPM refit (or on explicit trigger via script flag)
- Segment builder reuses the same DB session and data already loaded by the daily pipeline where possible

## 9. Known Limitations

1. **5v5 only.** PP/PK ratings are excluded. A player who is elite on the power play but average at 5v5 will have a mediocre offensive rating. PP deployment is critical for fantasy — this is addressed separately by the PP TOI features already in the opportunity model, not by RAPM. A future PP-specific RAPM could be built on the same infrastructure but with smaller sample sizes and different dynamics.

2. **Additive assumption.** RAPM assumes each player's contribution is independent and additive. Real hockey has line chemistry, systems effects, and non-linear interactions. The WOWY elevation metric (Phase 3) partially addresses this but is noisier. Genuine interaction effects (A is specifically good with B but not C) require even more data to estimate reliably.

3. **No causal identification.** RAPM controls for observed confounders (who's on ice) but not unobserved ones (why the coach chose this deployment). A player might rate highly because they're deployed in favorable situations, not because they're independently good. The zone-start exclusion is deliberate (we WANT deployment effects in the rating for the fantasy use case), but it means RAPM ratings are not pure talent measures.

4. **Temporal staleness.** Multi-season pooling means ratings lag reality. A player who improved dramatically mid-season will have their rating diluted by older data. Mitigation: exponential time-weighting could be added in a future iteration — weight recent games more heavily in the regression.

5. **Goalie exclusion.** Goalies are excluded from the design matrix. Goalie quality minimally affects xGF (which measures pre-shot quality, not saves), so this is acceptable for the offensive model. A defensive model would need to account for goalie effects.

6. **Rookie cold-start.** Players new to the NHL have no RAPM history. With a 400-min threshold, a rookie needs roughly a full season of regular play before they qualify for a rating. Until then they're treated as league-average. This aligns with the existing rookie evaluation gap noted in `docs/upside-and-opportunity.md`.

7. **Elevation noise.** The WOWY elevation metric is inherently noisier than raw RAPM because it's a difference-of-differences. With 4 seasons of data and 100-min pair thresholds, we expect enough qualifying pairs for most regular players, but elevation scores for players with limited linemate variation will be less reliable.

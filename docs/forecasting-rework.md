# Forecasting Rework: NHL API Event-Based Advanced Stats

## Motivation

The current forecasting pipeline depends on Natural Stat Trick (NST) for all
game-level stats (individual and on-ice). NST scraping is rate-limited (~20-35s
per player, daily budget caps) which means:

- We can never have real-time coverage of all ~900 active skaters.
- On-ice features are already disabled (`RollingOnIceExtractor` commented out)
  because scraping both individual + on-ice game logs doubles the request budget.
- We're one IP ban away from losing our primary data source.

**Goal:** Replace NST dependency with our own advanced stats engine built on NHL
API play-by-play and shift data. This gives us unlimited, reliable access to
event-level data we can process into the same (and better) metrics — including
our own expected goals (xG) model.

## NHL API Data Available

### Play-by-Play (`/v1/gamecenter/{gameId}/play-by-play`)
~250-300 events per game. Each event includes:
- **Event type**: `shot-on-goal`, `missed-shot`, `blocked-shot`, `goal`,
  `faceoff`, `hit`, `giveaway`, `takeaway`, `penalty`, `stoppage`
- **Coordinates**: `xCoord`, `yCoord` (ice surface location)
- **Zone**: `zoneCode` (O/D/N for offensive/defensive/neutral)
- **Shot type**: `wrist`, `slap`, `snap`, `tip-in`, `backhand`, `deflected`
- **Situation code**: 4-digit string encoding skater counts
  (e.g., `1551` = 1 goalie + 5 skaters each side)
- **Player IDs**: shooter, blocker, assisters, faceoff winner/loser, etc.
- **Timestamps**: period, time in period

### Shift Charts (`/stats/rest/en/shiftcharts?cayenneExp=gameId={id}`)
~600-700 shifts per game. Each shift has:
- `playerId`, `period`, `startTime`, `endTime`, `duration`
- `teamId`, `teamAbbrev`

### Game Logs (`/v1/player/{id}/game-log/{season}/2`)
Per-player season game log with: goals, assists, shots, hits, blocks, PIM, TOI,
PP goals/points, SH goals/points, shifts. ~80 games per player.

### Season Summaries (`/stats/rest/en/skater/summary` and `/skater/realtime`)
Aggregate stats for all ~900 skaters in a single request. Includes hits, blocks,
giveaways, takeaways, TOI per game, plus per-60 rates for realtime stats.

## What Advanced Stats We Can Derive

By combining play-by-play events with shift data, we can compute:

### Tier 1: Direct from events (straightforward)
| Metric | Source Events | Notes |
|--------|--------------|-------|
| Corsi For/Against (CF/CA) | shots + missed + blocked | All unblocked shot attempts |
| Fenwick For/Against (FF/FA) | shots + missed | Excludes blocked shots |
| Shots For/Against | shot-on-goal events | |
| Scoring Chances | shots from high-danger zones | Zone + coordinate filtering |
| High-Danger Chances | shots from slot area | xCoord/yCoord within slot |
| Zone starts (O/D/N%) | faceoff locations | Per-player from shifts |
| Individual stats (iCF, iSF, etc.) | events where player is shooter | |
| Hits, blocks, giveaways, takeaways | direct event types | |
| PP/PK/5v5 splits | situationCode filtering | `1551`=5v5, `1451`=PP, etc. |

### Tier 2: Requires shift-event correlation (moderate complexity)
| Metric | How |
|--------|-----|
| On-ice CF%, FF%, SF% | Cross-reference shifts with events to find who was on ice for each event |
| On-ice GF/GA | Goals that happened during a player's shift |
| Individual Points Percentage (IPP) | Player's points / team goals while on ice |
| TOI by situation (5v5, PP, PK) | Filter shifts by situation code at each timestamp |
| Relative stats (CF% rel) | Player's on-ice CF% minus off-ice CF% |

### Tier 3: Expected goals (xG)
| Metric | How |
|--------|-----|
| xG (expected goals) | ML model trained on shot location, type, game state, angle, distance |
| Individual xG (ixG) | xG for a player's own shots |
| On-ice xGF/xGA | xG for/against while player on ice |

## Situation Code Format

The `situationCode` field is a 4-character string with positions:
- [0] = away goalie count (0 or 1)
- [1] = away skater count
- [2] = home skater count
- [3] = home goalie count (0 or 1)

Examples:
- `1551` = normal 5v5 (1 goalie + 5 skaters each)
- `1451` = away team on power play (home has 4 skaters)
- `1541` = home team on power play (away has 4 skaters)
- `0551` = away empty net (6v5)
- `1560` = home empty net, away pulled goalie (unusual)

This lets us split all stats by game state without any guesswork.

## xG Model: Research & Strategy

### Landscape of existing models

| Model | Type | AUC | Log Loss | Features | Data |
|-------|------|-----|----------|----------|------|
| **Evolving Hockey** | 4x XGBoost (per situation) | 0.782 (5v5) | 0.185 | 43 features | 7-10 seasons |
| **gboparai (open source)** | XGBoost + Optuna | 0.818 (overall) | 0.194 | 56 features | 9 seasons |
| **MoneyPuck** | Gradient boosting | Not published | Not published | ~15 features | 2007-2015+ |
| **HarryShomer (open source)** | Gradient boosting | ~0.72 | 0.207 | 13 features | 2007-2016 |
| **JNoel71 (open source)** | LightGBM + Optuna | 0.779 (5v5) | — | 28 features | 2010-2020 |
| **Hockey-Graphs** | With pre-shot passing | 0.797 | 0.178 | +pass tracking | Manual data |

The realistic target with publicly available play-by-play data is **0.78-0.80 AUC**.
Getting above 0.80 likely requires player tracking data (positions, puck velocity)
which the NHL does not expose publicly. The ceiling for public data models is ~0.82.

### Benchmarking against closed-source models

**MoneyPuck publishes per-shot xG values** as free CSV downloads at
moneypuck.com/data.htm — ~1.84 million shots from 2007-2025, with 124 attributes
per shot including their `xGoal` probability. This lets us:

1. Run our model on the exact same shots and compare AUC/log-loss head-to-head
2. Use their arena-adjusted coordinates as a reference for our own coordinate
   adjustments
3. Identify where our model diverges (e.g., which shot types or zones we
   over/under-predict)

NST and Evolving Hockey only publish aggregate xG (player/team totals), not
per-shot — so MoneyPuck is the primary benchmark.

### Key techniques to incorporate from best models

1. **Separate models per game state** (Evolving Hockey approach) — train distinct
   XGBoost models for 5v5, PP, PK, and empty net. Feature importance differs
   dramatically between situations.

2. **Venue coordinate adjustments** (MoneyPuck, HockeyViz) — different arenas have
   systematic biases in where shots are recorded ("scorer bias"). HockeyViz
   published a correction methodology using weighted multi-season rink bias.

3. **Rebound/flurry handling** (MoneyPuck) — use angle-change-divided-by-time
   since last event rather than a simple binary rebound flag. Captures quality of
   second chances more accurately.

4. **Pre-shot event sequences** (Hockey-Graphs) — what happened 1-2 events before
   the shot (faceoff win, takeaway, pass). Improved AUC from 0.77 to 0.797 in
   their study.

5. **Feature engineering depth** (gboparai) — era flags for rule changes, shift
   fatigue via TOI, spatial zone flags (slot, high-danger area, behind net),
   score state effects.

### xG model features (planned)

**Geometric (from event coordinates):**
- Shot distance to net center
- Shot angle to net
- Is slot shot (high-danger area flag)
- Coordinate zone (inner slot, outer slot, point, behind net)

**Shot context:**
- Shot type (wrist, slap, snap, tip-in, backhand, deflected, wrap-around)
- Game state / situation code (5v5, PP, PK, empty net)
- Score differential at time of shot
- Period
- Is home team

**Sequence features (from prior events):**
- Time since last event
- Distance from last event
- Last event type (shot, faceoff, hit, giveaway, takeaway)
- Angle change from last shot (rebound quality)
- Is rebound (shot within 3 seconds of prior shot on goal)
- Is rush (shot within N seconds of neutral zone event)
- Number of prior shot attempts in last 10 seconds (flurry)

**Venue adjustment:**
- Arena-adjusted x/y coordinates (correct for scorer bias)

### Evaluation approach

- **Primary metrics:** AUC-ROC, log loss, Brier score
- **Calibration:** predicted vs actual goal rate by decile bucket
- **Temporal holdout:** train on seasons N through N+K, test on season N+K+1.
  Never random splits (prevents data leakage from same-game correlation).
- **Benchmark:** compare per-shot predictions against MoneyPuck xG values on
  the same shots
- **Training data:** 7-10 seasons (~700K-1M shots). NHL API has play-by-play
  back to at least 2010-11.

### What we can't compete on (without tracking data)

The NHL EDGE system tracks puck at 60fps and players at 15fps, generating ~1M 3D
coordinates per game. This data is NOT publicly available (only aggregated metrics
like skating speed and distance are exposed at edge.nhl.com). Without it we cannot
model:
- Defender positioning / screening at time of shot
- Puck velocity
- Pre-shot puck movement trajectories
- Zone entry type and forechecking pressure

This is why the ceiling for public-data models is ~0.80 AUC. The gap between 0.80
and whatever MoneyPuck/Evolving Hockey achieve internally likely comes from
proprietary tracking data or manually tracked features.

## Proposed Architecture

### New Tables

```
game_events
  - game_id, event_id (PK)
  - period, time_in_period, time_remaining
  - event_type (shot-on-goal, missed-shot, blocked-shot, goal, hit, etc.)
  - situation_code
  - x_coord, y_coord, zone_code
  - player_1_id (shooter/hitter/winner)
  - player_2_id (blocker/hittee/loser, nullable)
  - team_id
  - shot_type (nullable)
  - detail (JSON for extra fields)

player_shifts
  - game_id, player_id, shift_number (PK)
  - period, start_time, end_time, duration
  - team_id

game_advanced_stats (computed, per player per game)
  - game_id, player_id, situation (PK)  -- situation: 5v5, PP, PK, all
  - toi_seconds
  - individual: goals, assists, shots, missed_shots, blocked_shots_for,
    hits, blocks, giveaways, takeaways, icf, iff, iscf
  - on_ice: cf, ca, ff, fa, sf, sa, gf, ga, scf, sca, hdcf, hdca
  - rates: cf_pct, ff_pct, sf_pct, gf_pct, scf_pct, xgf, xga
  - zone_starts: oz_pct, dz_pct, nz_pct

shot_attempts (training data for xG model, one row per shot attempt)
  - game_id, event_id (PK)
  - shooter_id, goalie_id (nullable for empty net)
  - team_id, opponent_team_id
  - period, time_in_period, situation_code
  - x_coord, y_coord (raw), x_adj, y_adj (venue-adjusted)
  - distance_to_net, angle_to_net
  - shot_type
  - is_goal (boolean — the label)
  - score_differential (shooter's team perspective)
  - is_home
  - time_since_last_event, distance_from_last_event
  - last_event_type, last_event_x, last_event_y
  - angle_change_from_last_shot
  - is_rebound, is_rush, flurry_count
  - xg (predicted — populated after model is trained)
```

### Ingestion Pipeline

```
NHL API  -->  game_events + player_shifts  -->  compute engine  -->  game_advanced_stats
                (raw event storage)           (aggregation)        (per-player per-game)
                      |
                      +--> shot_attempts (feature-enriched) --> xG model --> xg predictions
```

1. **Fetch**: After each day's games, pull play-by-play + shifts for all
   completed games. ~15 games/day max, 2 requests per game = ~30 requests.
   At 0.5s delay = ~15 seconds.

2. **Store**: Raw events and shifts in normalized tables. This is our permanent
   record — we never need to re-fetch.

3. **Enrich**: Build `shot_attempts` table from events — compute derived features
   (distance, angle, rebound flag, sequence features) for each shot attempt.

4. **Predict**: Run trained xG model on shot attempts to populate `xg` column.

5. **Aggregate**: Compute `game_advanced_stats` including xGF/xGA from shot-level
   xG predictions, plus all Corsi/Fenwick/on-ice stats from shift-event
   correlation.

6. **Forecast**: Feature extractors read from `game_advanced_stats` for the
   player performance forecasting model.

### Shift-Event Correlation Algorithm

For each event in a game:
1. Look up the event's `timeInPeriod` and `period`
2. Find all player shifts that overlap that timestamp
   (`start_time <= event_time <= end_time` in the same period)
3. Tag the event with the set of on-ice player IDs
4. Aggregate: for each player, sum events that occurred during their shifts,
   split by situation code

This is the same approach NST and other analytics sites use.

## Implementation Phases

### Phase 1: Raw Data Ingestion
- [ ] Create `game_events` and `player_shifts` tables
- [ ] Write NHL API client functions for play-by-play and shift data
- [ ] Build ingestion script: fetch and store for a given game or date range
- [ ] Backfill: ingest 2024-25 and 2025-26 seasons (~2,600 games, ~5,200
      requests, ~45 minutes)
- [ ] Extended backfill: ingest 2015-16 through 2023-24 for xG training data
      (~10,400 games, ~20,800 requests, ~3 hours)

### Phase 2: xG Model
- [ ] Create `shot_attempts` table
- [ ] Build feature engineering pipeline: extract shot attempts from
      `game_events`, compute distance, angle, rebound, rush, sequence features
- [ ] Download MoneyPuck shot-level CSV for validation/benchmarking
- [ ] Train initial model (XGBoost) on 7+ seasons of data
- [ ] Train separate models per game state (5v5, PP, PK, empty net)
- [ ] Evaluate: AUC, log loss, calibration, temporal holdout
- [ ] Benchmark against MoneyPuck per-shot xG on same shots
- [ ] Implement venue coordinate adjustments if needed
- [ ] Populate `xg` column on all historical shot attempts

### Phase 3: Advanced Stats Engine (individual + on-ice)
- [ ] Create `game_advanced_stats` table
- [ ] Compute individual stats from events (iCF, iFF, shots, hits, blocks, etc.)
- [ ] Build the shift-event correlation engine for on-ice stats
- [ ] Compute on-ice stats: CF/CA, FF/FA, SF/SA, GF/GA per player per game
- [ ] Compute xGF/xGA per player per game using Phase 2 xG predictions
- [ ] Compute zone start percentages, scoring chances, high-danger chances
- [ ] Split all stats by situation (5v5, PP, PK, all) using situation codes
- [ ] Validation: compare our numbers against NST for overlapping games

### Phase 4: Forecasting Model Rebuild
- [ ] Update feature extractors to read from `game_advanced_stats`
- [ ] Retrain XGBoost forecasting model on new features (including xG-based)
- [ ] Backtest against old model to verify improvement (or at least parity)
- [ ] Wire up daily automated ingestion + stat computation after games

### Phase 5: Iteration & Refinement
- [ ] Add pre-shot passing/sequence features if initial model plateaus
- [ ] Experiment with shooter/goaltender skill adjustments
- [ ] Explore LightGBM / Optuna hyperparameter tuning
- [ ] Re-evaluate against MoneyPuck after each improvement round
- [ ] Consider era-aware features for rule changes over time

## Feasibility Assessment

**Definitely feasible.** The NHL API provides all the raw ingredients. The main
engineering efforts are:

1. **xG model (Phase 2)** — well-trodden ground with multiple open-source
   references. We can realistically target 0.78-0.80 AUC at even strength.
   Multiple open-source models achieve this. MoneyPuck's per-shot CSV provides
   a direct benchmark.

2. **Shift-event correlation (Phase 3)** — matching ~700 shifts against ~300
   events per game to determine who was on ice for each event. Well-understood
   problem, pure computation.

**Scale:** ~1,300 games per season, ~300 events + ~700 shifts per game. A full
season's raw data is ~400K events + ~900K shifts. For xG training, 10 seasons =
~1M shot attempts. All totally manageable in PostgreSQL.

**Backfill estimate:** ~13,000 games across 10 seasons, 2 API requests per game =
~26,000 requests at 0.5s each = ~3.5 hours total. One-time cost.

## Open Questions

- Do we keep NST data as a validation reference, or deprecate those tables
  entirely once we trust our own numbers?
- How far back do we want to backfill? 2015-16 onward (7 seasons) is the minimum
  for solid xG training. Going back to 2010-11 adds more data but older seasons
  may have different coordinate recording quality.
- Should we compute stats incrementally (after each game) or in batch
  (recompute full season periodically)?
- Do we want to start with MoneyPuck's enriched CSV data for initial xG model
  prototyping (faster iteration) before building our own feature pipeline from
  raw play-by-play?

## References

- MoneyPuck data downloads: moneypuck.com/data.htm (per-shot xG, 124 attributes)
- MoneyPuck methodology: moneypuck.com/about.htm
- Evolving Hockey xG model: evolving-hockey.com/blog/a-new-expected-goals-model-for-predicting-goals-in-the-nhl/
- gboparai/nhl-xg-model: github.com/gboparai/nhl-xg-model (top open source, 0.82 AUC)
- HarryShomer/xG-Model: github.com/HarryShomer/xG-Model
- JNoel71/NHL-Expected-Goals-xG-Model: github.com/JNoel71/NHL-Expected-Goals-xG-Model
- Hockey-Graphs pre-shot movement: hockey-graphs.com/2019/08/12/expected-goals-model-with-pre-shot-movement-part-1-the-model/
- HockeyViz scorer bias methodology: hockeyviz.com/txt/scorerBias
- Hockey Analysis model comparison: hockeyanalysis.com/2024/04/08/quick-comparison-of-four-public-expected-goal-models/

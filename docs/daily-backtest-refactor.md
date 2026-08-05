# Daily-Decision Backtest Refactor

Foundation document for refactoring the transaction backtest from a weekly snapshot into a day-by-day decision loop. This is the substrate every future evaluation, hyperparameter sweep, and agent comparison will run on, so it has to be right.

## Motivation

The current backtest locks every decision in on Monday morning of each fantasy week:

1. Build the FA pool, roster, and per-player projections once, as-of Monday.
2. Run `optimize_week()` to greedily pick up to 4 (add, drop) pairs from that frozen snapshot.
3. Score the picks against actual outcomes for the rest of the week.

This is fast and convenient but it isn't how anyone — human or agent — actually plays fantasy hockey:

- A real manager checks the league every morning, sees who exploded the night before, sees who's been scratched, sees confirmed goalie starters for tonight, and makes *one* decision at a time.
- Add slots can be held back deliberately. Tuesday looks dry → save the slot for Thursday.
- Player projections need fresh data: by Wednesday, Beniers' Monday and Tuesday lines should be in his rolling features.
- Goalie streaming is intrinsically a one-day decision.
- The 1–7 day window the optimizer values shrinks as the week burns down — Sunday morning the window is 1 day, not 7.

The Monday-snapshot model also hides time-leakage bugs (we can accidentally use Friday data to pick a Tuesday add and never notice) and produces transactions with no day attached, which makes side-by-side comparison against real Yahoo timestamps impossible.

If this project's goal is to *solve* fantasy hockey, the evaluation harness has to be at least as realistic as the way the game is played. A daily loop is the foundation; everything else (hyperparameter tuning, model comparisons, autonomous agent rollouts) sits on top of it.

## Target behavior

For each fantasy week in the backtest range, the simulator iterates day by day:

```
for week in weeks:
    roster_state    = roster_as_of(week.monday)
    adds_remaining  = 4
    for day in week.days:                       # Mon .. Sun
        fa_pool        = fa_pool_as_of(day)
        roster_state   = apply_lineups(roster_state, day)
        decision       = decide_today(
            roster=roster_state,
            fa_pool=fa_pool,
            adds_remaining=adds_remaining,
            days_left_in_week=week.days_remaining(day),
            as_of=day,
        )
        if decision:
            roster_state   = apply_swap(roster_state, decision)
            adds_remaining -= 1
            log(decision, decided_on=day)
```

Key properties:

- Every decision sees only data available at `as_of=day` — no leakage from the future.
- Each transaction is stamped with the day it was decided.
- Add-budget carries forward within a week and resets every Monday.
- The agent can choose to do nothing on a given day; that's a first-class output, not an absence.
- Multiple swaps in one day are permitted but rare (only when several genuine no-brainers stack up).

## Key design decisions

### 1. Knowledge cutoff is a first-class parameter

Every function that touches stats — `compute_player_value_simple`, `find_optimal_window_simple`, the forecast layer, drop ranking, replacement level — needs an `as_of: date` parameter that defines the latest date whose data the function may read. Defaults to `date.today()` for live use.

This is non-negotiable. Without it we can't trust any backtest result. Time leakage is the silent killer of fantasy/sports ML projects and the only defense is to plumb the cutoff through every call site and assert it in tests.

### 2. Single-day decision function replaces `optimize_week`

`optimize_week()` becomes `decide_today()`. Signature roughly:

```python
def decide_today(
    roster: Roster,
    fa_pool: list[PlayerValue],
    drop_candidates: list[PlayerValue],
    adds_remaining: int,
    days_left_in_week: int,
    aggression: AggressionLevel,
    as_of: date,
) -> list[TransactionCandidate]:  # 0, 1, or rarely >1
```

It returns the set of swaps to fire *today*. The decision is:

- Score every (add, drop) pair using the current quality/schedule formula.
- Compute today's opportunity-cost threshold from the current pool.
- Compute *expected best swap over the remaining days* (the deferral bar).
- Fire any swap whose score clears both the threshold and the expected future value.

This makes the deferral logic real instead of a stub. The current `_should_defer` heuristic only triggers when `slots_left > remaining_game_days`; in the daily loop, deferral happens implicitly any day no swap clears the bar.

### 3. Per-day roster and FA pool reconstruction

We need point-in-time views of:

- **Roster on day D**: walk the synced `YahooTransaction` log forward from the draft to day D, applying adds, drops, and trades. Yahoo records trades only on the receiving team (action="trade"), so both `get_my_roster_at` and `FAPoolReconstructor` must process all league transactions — not just the target team's — to detect outgoing trades.
- **FA pool on day D**: every NHL player not on any team's roster as of day D. Approximated from the same transaction log; for players we never see in any roster, assume FA throughout.

`get_my_roster_at` handles per-team reconstruction; `FAPoolReconstructor` handles league-wide roster membership with per-day caching. Both treat `action="trade"` as equivalent to `action="add"` for the receiving team.

### 4. Player projections must use rolling features as-of the cutoff

The forecast layer (currently `src/predict/forecasting/`) needs to support "compute features for player X as if today were `as_of`." This means the rolling-window aggregations (last-10 GP, last-5 GP, season-to-date) must respect the cutoff.

If the existing model assumes "today" implicitly, we patch it. If it pulls from precomputed feature tables, those tables need an `as_of_date` join key or we recompute on the fly. Performance trade-off — if recomputing per-day per-player is too slow, we precompute a per-day feature snapshot for the backtest range up front.

### 5. Each transaction carries a `decided_on` date

Add a field to `TransactionCandidate` (or wrap it in a new `LoggedTransaction` dataclass) that records the day the agent fired it. This unlocks:

- Side-by-side comparison against real Yahoo transactions matched on day.
- Per-day diagnostic plots ("how often does the agent fire on each weekday?").
- Realistic per-game-day attribution of FPTS produced.

### 6. Within-week add budget is mutable state

The simulator owns one piece of weekly state: `adds_remaining`. It starts at 4 every Monday and decrements with each fire. The decision function reads it but does not own it. This separation keeps `decide_today()` pure and testable.

### 7. Goalie streaming becomes natural

A daily loop is the right shape for goalie decisions. Today's goalie evaluation can use confirmed starter info, opponent matchup, and crease-share trend, all as-of-this-morning. No special path needed — goalies compete in the same candidate pool as skaters, just with `fillable_games=1` and a 1-day window.

## Phased implementation plan

Each phase is independently testable and leaves the backtest in a working state.

### Phase 1: Knowledge cutoff plumbing

**Goal**: thread `as_of: date` through every function that reads player or game data, with tests that prove no leakage.

- Add `as_of` parameter to:
  - `compute_player_value_simple` and `compute_player_value_window`
  - `find_optimal_window_simple`
  - `compute_replacement_level`
  - `rank_drops`, `get_drop_candidates`
  - `compute_position_scarcity` (no-op, but document why)
  - The forecasting layer's prediction entry point
- Update all DB queries inside those functions to filter by `Game.date <= as_of`.
- Add a leakage test: build a player value as-of `2026-01-15` and assert nothing returned references a game on or after `2026-01-16`.
- Behavior unchanged at this phase — backtest still runs weekly snapshots, just with explicit cutoffs.

This phase is the largest in line-count but the lowest-risk in semantics. It just makes existing implicit assumptions explicit.

### Phase 2: Daily decision function

**Goal**: introduce `decide_today()` alongside `optimize_week()` (don't delete the old one yet — keep both for A/B).

- Implement `decide_today()` per the signature above.
- The opportunity-cost threshold and expected-future-value calculation move inside it.
- Returns `list[TransactionCandidate]` (usually empty or length 1).
- Unit tests: thin pool → empty list. Rich pool → returns the one obvious add. Edge cases (no drops eligible, all FAs garbage, etc.).
- The deferral logic: compute "best swap I expect to see in the next N days" by projecting today's pool forward (weekend-of-the-week heuristic at first, refine later).

### Phase 3: Daily backtest loop

**Goal**: refactor `TransactionBacktester.run()` from week-by-week to week→day nested loop.

- Track simulated roster state day-to-day within a week.
- Reset `adds_remaining` every Monday.
- For each day, call `decide_today()` with `as_of=day`.
- Apply selected swaps to the simulated roster immediately so tomorrow's decision sees the post-swap state.
- Stamp every fired transaction with `decided_on=day`.
- Score each transaction against actual FPTS *from its decision day forward*, not from Monday.

The output schema changes: instead of one `WeekBacktestResult` per week, we have a flat list of stamped transactions plus weekly aggregations derived from them.

### Phase 4: Per-day FA pool reconstruction

**Goal**: replace "Monday FA snapshot" with "as-of-day FA pool."

- Build a `FAPoolReconstructor` keyed on `(league, day)` that walks `YahooTransaction` to determine roster membership for every team on every day.
- Anyone not on any team on day D is in the FA pool that day.
- Cache aggressively — the diff from day D to day D+1 is tiny.
- Validate: pick a random day, compare reconstructed roster against Yahoo's actual roster snapshot (we have these from sync) and assert match.

### Phase 5: Forecast features as-of cutoff

**Goal**: ensure every projection respects the cutoff in its rolling features.

- Audit the forecasting feature builders for hardcoded "today" or "season-to-date through latest game."
- Add `as_of` to feature extraction; recompute rolling stats at that boundary.
- If precomputed feature tables exist and lack a date dimension, either rebuild them with one or compute on-the-fly inside the backtest.
- Performance benchmark: how long does one day's worth of FA projections take? If > a few seconds, add caching.

### Phase 6: Reporting and metrics

**Goal**: turn the daily transaction log into useful diagnostics.

- New report format: per-day transaction log per week, with reasoning, expected vs actual.
- Aggregations: per-aggression-mode total FPTS, adds used, adds skipped, day-of-week distribution.
- Side-by-side: align each agent transaction with the closest user transaction on the same day.
- New metrics:
  - **Hold accuracy**: of players the agent declined to drop, how many would have outperformed their replacement?
  - **Timing efficiency**: did the agent fire adds on the days that maximized window coverage?
  - **Idle-day rate**: how often does the agent correctly do nothing?
- Persist results to a structured format (JSONL or similar) so downstream sweeps can ingest them.

### Phase 7: Validation and sign-off

- Time-leakage test suite passes.
- Reconstructed FA pools match Yahoo snapshots on spot-checks.
- A run with a "perfect oracle" forecast (replace forecast with actual) should produce a near-optimal allocation — sanity check on the optimizer.
- A run with the current forecast should produce transactions stamped on plausible days (not 100% Mondays).
- Backtest matches or beats the human baseline (`McChuckin'`) at AGGRESSIVE/DESPERATE levels and approximately ties at NORMAL.

Once Phase 7 passes, delete `optimize_week()` and the old weekly path. From then on, `decide_today()` is the only entry point.

## Risks and open questions

- **Time leakage is the killer.** Without rigorous tests, a single forgotten DB filter invalidates every backtest result we'll ever produce. The Phase 1 test must be aggressive and run in CI.
- **Forecast model wasn't designed for arbitrary cutoffs.** Phase 5 may surface that the existing rolling features bake in the latest available data implicitly. Worst case we retrain or rewrite the feature pipeline.
- **Per-day FA reconstruction may be incomplete.** The `YahooTransaction` log might not cover every team's full transaction history (we sync our own team in detail; others less so). We may need to approximate or ingest more Yahoo data. **Resolved:** Yahoo records trades with `action="trade"` only on the receiving team. Both `get_my_roster_at` and `FAPoolReconstructor` now process all league transactions and handle the trade action type, so outgoing trades are correctly detected. Spot-check tests verify strict subset consistency across 5 dates.
- **Performance.** Daily granularity is roughly 7x more decision calls. With 50 FAs each requiring `find_optimal_window_simple`, plus per-day forecast feature recomputation, a 14-week backtest could go from seconds to minutes. Profile early; cache aggressively.
- **Goalie data freshness.** The agent's daily decisions on goalies depend on confirmed-starter information that was true *that morning*. We don't have historical Daily Faceoff snapshots. We'll either approximate (assume the goalie who actually started was confirmed) or accept the limitation.
- **Decision deadline within a day.** Real fantasy decisions happen before that night's lineup lock. The simulator should make decisions before any of that day's games count toward player stats. Enforce this in the cutoff logic: `as_of=day` means "data from games before day, not on day."
- **Multiple swaps per day.** Allowed but should be rare. Need to make sure the optimizer doesn't fire 4 adds in a single morning just because the math supports it — that's not how humans play and probably indicates a flaw.

## Success criteria

The refactor is done when:

1. Every transaction in the backtest output has a `decided_on` date that's not always Monday.
2. The leakage test suite passes and is wired into CI.
3. Reconstructed FA pools match Yahoo snapshots on at least 5 spot-checked days.
4. The day-of-week distribution of agent transactions looks roughly human (heavier on Mon/Tue/Thu, lighter on weekends).
5. NORMAL mode matches the user's transaction count within ~30% across the backtest range.
6. AGGRESSIVE mode beats the user's net FPTS (it does today, but with a more credible methodology).
7. The structured output is consumable by a future hyperparameter-sweep harness.

Once those are met, this becomes the foundation for everything else: hyperparameter tuning, agent A/B testing, autonomous-mode rollout, and eventual live deployment. Get this right and every downstream evaluation inherits its credibility. Get it wrong and we'll be chasing leakage bugs for months.

# P7: Lifecycle (replan, persistence, execution, cleanup)

**Owns:** `src/optimize/week/lifecycle.py`, a new `week_plans` table and migration, `src/api/routers/agent.py`, and all deletions listed below

**Depends on:** P6

**Blocks:** nothing

Read `00-overview.md` first.

## Purpose

Three jobs that all live at the boundary of the optimizer:

1. **Replanning.** Detect that the world changed, regenerate, and explain what changed and why.
2. **Persistence.** Store plans so the agent monitor UI has something real to read and so there is an audit trail.
3. **Cleanup.** Delete the superseded code and migrate everything still pointing at it.

The third is the largest and it is not optional.
Leaving two decision engines in the tree is how the codebase ends up with three.

## Part 1: Plan lifecycle

### State fingerprint

P0 declares `WeekPlan.state_fingerprint` as a string.
Define it here as a stable hash over the parts of `WeekState` that, if changed, would invalidate a plan:

- Roster membership, ours and the opponent's
- Availability of every candidate referenced in the plan
- Injury status of every rostered player
- Schedule for the window
- Adds remaining, both sides
- Earned scores, both sides

Deliberately excluded: `as_of` itself, and projection values that moved by a trivial amount.
Otherwise every run produces a "changed" verdict and the diff becomes noise.

### Triggers

```python
def needs_replan(current: WeekPlan, state: WeekState) -> tuple[bool, list[str]]
```

Returns whether to replan and human-readable reasons.

| Trigger | Why |
|---|---|
| A target in the plan is no longer available | Another manager took them. Fall back to `PlannedMove.alternates` first, replan only if none survive. |
| A good player hit the wire | The spec's point that other managers' mistakes are our opportunity. |
| Injury news on a rostered player | May open the IR path or change hold value. |
| Injury news on any player | A starter going down makes their backup valuable. |
| Goalie start confirmed | The confirmation window in P3. Can convert a speculative add into a firm one. |
| Opponent made a transaction | Changes their projection, changes P(win), can change posture. |
| Posture changed | Different window or different floor means a different plan entirely. |
| Schedule change | Postponements are rare but real. |
| Day rollover | Fire dates advance. |

A cheap-check ordering matters here: the agent loop polls every few minutes and most polls should exit in milliseconds.
Compare fingerprints first, and only run the detailed reason analysis when they differ.

### Diffing

```python
def diff_plans(previous: WeekPlan, current: WeekPlan) -> PlanDiff
```

The interesting output for the monitor UI is not the new plan, it is what changed and why.
Produce statements a person can read:

```
Thursday's add changed from Player X to Player Y.
  Player X was picked up by another team on Tuesday.
  Player Y was the ranked alternate and projects 0.8 FPTS lower.

Aggression rose from NORMAL to AGGRESSIVE.
  Opponent added a player with three remaining games; P(win) fell from 0.61 to 0.44.
```

## Part 2: Persistence

New table `week_plans` in `src/core/models/`, plus an Alembic migration.

```
week_plans
  id                  PK
  plan_id             uuid, unique
  league_key          str
  team_key            str
  yahoo_week          int
  generated_at        timestamp
  state_fingerprint   str, indexed
  posture_mode        str
  window_start        date
  window_end          date
  aggression          str
  p_win               float
  baseline_p_win      float
  projected_p_win     float
  conviction          float
  moves               JSONB
  reasoning           JSONB
  status              str    active | superseded | executed | abandoned
  superseded_by       FK week_plans.id, nullable
```

Plans are append-only.
A replan writes a new row and marks the old one `superseded`, pointing at its successor.
The chain is the audit trail, and it is the interesting content for the monitor page.

Follow the existing Alembic conventions in `alembic/versions/`.

## Part 3: Execution boundary

```python
def execute(session, move: PlannedMove, state: WeekState, dry_run: bool = True) -> ExecutionResult
```

The only place in the system that writes to Yahoo.

Guardrails, all from `docs/autonomous-agent.md`:

- **Dry run by default.** Executing for real must be an explicit opt-in.
- **Daily transaction cap**, configurable.
- **Kill switch** the owner can flip instantly.
- **Protected players are re-checked at execution time**, not just at plan time. Defense in depth: if a bug upstream ever proposes dropping a protected player, this is the last thing standing between the bug and a real transaction.
- **Full audit log** of every attempt, including refusals and failures.
- **Re-validate against live state before firing.** The plan may be seconds old and the target may have just been taken. Never execute blind off a stored plan.

Yahoo write scope is not yet wired up.
Build the interface and the guardrails, stub the actual API call, and make `dry_run=False` raise a clear error until the write client exists.

## Part 4: Cleanup

The largest and least glamorous part.
Do it, and do it completely.

### Delete

| File | Notes |
|---|---|
| `src/optimize/daily.py` | Parallel decision engine. `decide()`, `FIRE_THRESHOLDS`, `INJURY_PENALTY_MULT`, `StrategyConfig`, `FACandidate`, `Transaction`. |
| `src/optimize/roster_state.py` | Absorbed into `WeekState` by P1. |
| `src/optimize/drops.py` | Absorbed by P4. |
| `src/optimize/replacement.py` | Absorbed by P4. |
| `src/optimize/goalies.py` | Absorbed by P3. |
| `src/optimize/week/heavy.py` | Superseded by P6. |

### Shrink

`src/optimize/value.py` is 742 lines and most of it is dead once the grid exists.

- Schedule helpers (`get_team_week_games`, `get_team_games_in_window`, `get_team_remaining_games`) survive, or move to `src/core/queries/schedule.py` where they arguably belong.
- `load_roster_from_yahoo` survives; `src/optimize/sync.py` uses it.
- `compute_player_value`, `compute_player_value_window`, `compute_player_value_simple`, `find_optimal_window`, `find_optimal_window_simple`, `can_player_fill_slot`, `_default_forecast_fn`, `_get_forecast_deps`, and the `_FORECAST_CACHE` global all go. The grid replaces them.

`src/optimize/models/plan.py`: remove `AGGRESSION_WEIGHTS`, `TransactionCandidate`, and the old `WeekPlan`. Keep `TeamWeekResult`, which `week/light.py` still returns.

### Migrate callers

Every one of these currently imports something that is going away.
Track them; missing one breaks the backtest silently.

| Caller | What it uses | Action |
|---|---|---|
| `src/backtest/strategies.py` `SimpleValueStrategy` | `compute_player_value`, `compute_replacement_level`, `get_drop_candidates`, `plan_week` | Repoint at `optimize_week()`. |
| `src/backtest/strategies.py` `PuckAgentStrategy` | `compute_roster_state`, `daily.decide` | Repoint at `optimize_week()`. If the two strategies become identical, collapse them and say so. |
| `src/backtest/engine.py` | `AggressionLevel`, `RosterSlotSettings` | `PREPARE` is gone; check the `auto_aggression` path. |
| `src/backtest/simulation.py` | `AggressionLevel` | Same. |
| `scripts/run_backtest.py` | `AggressionLevel` | Same. |
| `src/api/routers/yahoo.py` | `assign_players_to_slots` | P1 kept the signature. Verify. |
| `src/optimize/sync.py` | `load_roster_from_yahoo` | Survives. Verify. |
| `scripts/sync_valuations.py`, `scripts/daily_pipeline.py` | `src.optimize.sync` | Unaffected, but run them. |

`tests/test_layering.py` enforces the layer boundaries. It must still pass.

### Documentation

- `CLAUDE.md`: rewrite the Optimization section. Remove the "Known leakage bug" note once P3 has fixed it.
- `docs/autonomous-agent.md`: the Transaction Evaluator section describes the old scoring formula with `week_weight` and `ros_weight`. It is now wrong. Rewrite it against the three-layer objective.
- `docs/aggression-level.md`: still describes `PREPARE` as an `AggressionLevel`. Update to the mode-plus-aggression split.
- `docs/tasks.md`: update the weekly optimizer line.

## Part 5: API surface

`docs/autonomous-agent.md` describes `frontend/src/pages/Agent.jsx` as running entirely on hardcoded constants with no backend.
This package makes the first real data available.

Add `src/api/routers/agent.py` with typed Pydantic response models in `src/api/schemas.py`, following the existing conventions:

- `GET /api/agent/plan` current active plan
- `GET /api/agent/plan/history` the supersession chain for a week
- `GET /api/agent/plan/{plan_id}/diff` what changed against its predecessor

Do not touch the frontend.
The owner iterates on UI frequently and separately; ship the endpoints and let that happen on its own schedule.

## Acceptance

- `pytest` passes in full, including `tests/test_layering.py` and every leakage test in `tests/backtest/test_leakage.py`.
- `grep -rn "daily\.decide\|compute_roster_state\|AGGRESSION_WEIGHTS\|score_transaction\|PREPARE" src scripts tests` returns nothing.
- A backtest over a real week range produces the same or better results than the pre-rebuild baseline. Capture the before number first, because after the deletions you cannot go back and measure it.
- `execute(dry_run=True)` produces a complete audit record and touches nothing.
- `execute(dry_run=False)` raises until the Yahoo write client exists.
- Replan triggers fire on each condition in the table, tested individually.
- `needs_replan` returns `False` in under 50ms when the fingerprint is unchanged.

## Do not

- Do not change planner logic. If a bug shows up during migration, report it against P6 rather than patching it here.
- Do not build the Yahoo write client. Interface and guardrails only.
- Do not modify `frontend/`.
- Do not skip a deletion because something still imports it. Migrate the caller. The whole point of this package is that the old engine is gone.

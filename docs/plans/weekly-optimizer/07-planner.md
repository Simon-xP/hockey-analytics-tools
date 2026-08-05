# P6: Planner (generate, score, select)

**Owns:** `src/optimize/week/generate.py`, `src/optimize/week/score.py`, `src/optimize/week/select.py`, `src/optimize/week/__init__.py`

**Depends on:** P0, P1, P2, P3, P4, P5

**Blocks:** P7

Read `00-overview.md` and `01-contract.md` first, then skim the other package plans so you know what each interface guarantees.

## Purpose

The brain.
Given a state, a posture, a candidate pool, and a drop floor, produce a **dated plan** with ranked alternates and a decision about what fires today.

Everything below you measures.
You are the only package that chooses.

## Public entry point

`optimize_week()` in `src/optimize/week/__init__.py` keeps its current signature and dispatch behavior.
`src/api/routers/` and `src/backtest/` call it and should not need to change.

Internally, the heavy path is replaced wholesale:

```
optimize_week_heavy()
  |
  +-- build_week_state()        P1
  +-- determine_posture()       P5   -> mode, window, aggression
  +-- build_grid(window)        P1
  +-- candidates = supply + goalies   P2 + P3
  +-- hold values + floor       P4
  +-- generate()                you
  +-- score()                   you
  +-- select()                  you
  -> PlanSet
```

`src/optimize/week/heavy.py` is superseded. Leave it importable; P7 deletes it.

## Part 1: Scoring

`score.py`. Build this before the search, because the search calls it constantly.

### CONTEST mode

```
delta_p_win(plan) = P(win | plan) - P(win | do nothing)
```

`P(win)` comes from `matchup/win_probability.py`, which already takes both teams' `(mu, sigma)` plus pickup boosts.
Our side comes from the plan's grid. The opponent's side comes from `week/light.py`.

**Cache the opponent once per run.**
`project_team_remaining()` and `model_pickup_boost()` call `forecast_player` in a loop and are expensive.
The opponent's distribution does not vary across our beam states, so compute it once at the top and pass it down.
Doing this per state will make the planner unusably slow.

### PUNT mode

There is no current-week matchup to win, and next week's opponent roster is not yet knowable.
Maximize expected points over the window instead:

```
score(plan) = grid.mu()
```

Note in the module docstring that scoring PUNT against next week's actual matchup is a possible future upgrade, since Yahoo publishes the schedule ahead. Do not build it.

### The terminal value tiebreak

```
if abs(delta_p_win(a) - delta_p_win(b)) < EPSILON:
    prefer the higher terminal value
```

Read the "Terminal value" and "There is no Sunday rule" sections of `00-overview.md` before you touch this.

`EPSILON` is a real tuning parameter and it is dangerous in both directions.
Too large and terminal value starts overruling live win-probability differences, which is exactly the failure the owner flagged: refusing a one-day Sunday stream in a tight matchup because something else helps next week.
Too small and the tiebreak never fires and the planner picks arbitrarily among equivalent moves.

Start near `0.005` (half a percentage point of win probability) and validate against the acceptance scenarios.
Log every time the tiebreak decides a move, so the calibration is visible.

## Part 2: Generation

`generate.py`. Beam search over dated move sequences.

### Search state

```python
@dataclass(frozen=True)
class SearchNode:
    day: date              # the day currently being expanded
    grid: LineupGrid       # complete grid for the whole window, moves applied
    roster: frozenset[int]
    adds_used: int
    moves: tuple[PlannedMove, ...]
    score: float
```

The grid always spans the **whole window**, even mid-search.
A move applied on day `d` propagates forward through `with_move`, so a partial plan already has a complete, scoreable grid.
That means every node has a real `delta_p_win` and the beam ranks on the true objective rather than a heuristic.

### Expansion

From a node, generate successors:

1. **Advance.** Move to the next day, no transaction. Always legal.
2. **Add and drop.** For each candidate available on this day and each roster player the floor permits, apply the swap on this day. Costs one add.
3. **Free add.** Candidate into an open active spot. Costs one add, no drop.
4. **IR move plus free add.** An IR-eligible injured player goes to IR, opening a spot. Ask P4 for this; do not detect it yourself.

Staying on the same day after a transaction is legal, which is how multi-move Mondays get found.
Bound same-day moves at `adds_remaining` and let the score decide whether they are worth it.

### Legality

- A candidate with `available_from > day` cannot be added on that day. Waiver players remain in the pool for later days.
- A player above the floor never appears as a drop. P4 already filtered them out; do not re-check.
- **Chains are legal and expected.** A player added Monday is on the roster Thursday and is a legal drop then. This is the spec's worked example and the search must find it. Verify it does.
- Adds cannot exceed `state.adds_remaining`.

### Availability decay

The honest reason to fire early rather than defer.

A move planned for Thursday only pays off if the target is still there on Thursday.
Discount deferred moves by the probability the candidate survives:

```
p_available(candidate, days_ahead)
```

Estimate it from ownership pressure: recent ownership delta, roster percentage, and how many managers have adds left.
Yahoo trending data already flows in via `get_trending_players`.

This replaces `_should_defer()` in `heavy.py`, which guessed.
Note the structural point: you do **not** need an explicit option-value term for holding an add. The beam already compares firing now against firing later as two branches, so option value is implicit in the search. What is *not* implicit, and what you must add, is the risk that a deferred target disappears.

Adds expire at week end, so holding one past `week_end` is worth exactly zero. That falls out with no special case.

### Beam width

Start at 20 and measure.
Compare against exhaustive search on small synthetic problems (2 adds, 3 days, 10 candidates) and report how often the beam finds the exact optimum.
If it is below 95%, widen it or improve the expansion ordering.

Report the number in the test output. Do not silently cap coverage; `00-overview.md` requires that truncation be visible.

## Part 3: Selection

`select.py`.

### Conviction

How sure are we about the first move.

```python
def conviction(best: WeekPlan, runners_up: Sequence[WeekPlan]) -> float
```

Three inputs:

- **Margin.** How far `delta_p_win` of the best plan sits above the second-best plan that starts with a *different* first move. A plan that wins by a hair is not a conviction signal.
- **Candidate confidence.** `Candidate.confidence`, especially for goalies where an unconfirmed start is genuinely a coin flip.
- **Robustness.** Perturb the projections (a modest relative shock) and re-rank. If the first move survives, conviction is higher. Keep this cheap; a handful of perturbations is enough.

### Fire now

A move fires when `fire_date == as_of` **and** conviction clears the bar.
Everything later stays pending in the plan for the UI and for the next run to reconsider.

The bar is not a constant.
It should fall as the option to act expires: on the final day of the window with adds still unspent, anything net-positive should fire, because an unused add is worth nothing.
That is a consequence of expiry, not a calendar rule.

### Alternates

Two levels, both required by the spec.

- `PlannedMove.alternates`: for the chosen `(fire_date, drop)`, the next best adds. This is what the agent falls back to when someone takes the target.
- `PlanSet.alternates`: whole plans with a different shape. Deduplicate by first move, otherwise you get four near-identical plans.

The spec asks to see options 1 through 4. Return at least four when they exist.

## Acceptance scenarios

`tests/optimize/week/test_planner.py`.
Build these on synthetic states with hand-computed projections so the expected answer is unambiguous.

**The spec's worked example**

- `finds_the_chained_two_step`: construct the exact scenario from `docs/weekly-optimizer.md`. Player A plays Tue/Thu/Sat and is barely above replacement. B plays Mon/Wed/Sat with Mon and Wed open. C plays Tue/Thu/Fri/Sun. Lineup is full Tue and Sat. The plan must be: drop A for B on Monday, drop B for C on Thursday. If the search cannot find this, it is not finished.

**The Sunday case, the one the owner raised**

- `close_matchup_on_the_final_day_takes_the_one_day_stream`: trailing by 4 on the last day, a one-game streamer available. The plan fires the stream. Terminal value must not suppress it.
- `decided_matchup_on_the_final_day_targets_next_week`: same day, trailing by 60. Posture is PUNT, window is next week, the plan picks the best next-week schedule. Confirm this comes from posture and not from a date branch anywhere in your code.

**Variance behavior, the payoff for the objective change**

- `trailing_late_prefers_variance`: two candidates, equal mean, different variance, trailing. The volatile one wins.
- `leading_late_prefers_the_floor`: same pair, leading. The steady one wins.
- `equal_when_the_matchup_is_a_coin_flip_early`: Monday, tied, high sigma. The two are close, because variance barely matters when the window is long.

**Budget and timing**

- `spends_all_four_adds_on_monday_when_that_is_optimal`: the spec's four-droppable-players scenario.
- `defers_when_the_target_is_safe_and_a_better_option_is_likely`: high `p_available`, valuable later slot.
- `fires_immediately_when_the_target_is_likely_to_be_taken`: low `p_available`.
- `does_not_leave_adds_unspent_on_the_final_day` when a positive move exists.

**Constraints**

- `never_drops_above_the_floor`: fuzz across many random states and assert no plan ever contains one. This is the safety net for the whole system.
- `waiver_player_is_only_addable_after_clearing`, and a plan that schedules the add for exactly the clear date is valid.
- `ir_move_taken_when_available` in preference to a drop.

**Performance**

- `full_run_under_the_budget`: a realistic state (roster of 15, 50 candidates, 7-day window, 4 adds) completes in under 10 seconds with warm projections. The agent loop polls every few minutes, so this is the ceiling that makes the whole design viable. If it fails, the projection cache in P1 is not doing its job; profile before optimizing the search.

## Do not

- Do not branch on the day of the week. Anywhere. See `00-overview.md`.
- Do not reintroduce aggression as a weight. It sets the floor in P4 and has no other role.
- Do not call `forecast_player()`. Read the projection cache.
- Do not compute hold values, replacement level, or candidate projections yourself. Consume P2, P3, and P4.
- Do not execute anything or write to the database. P7 owns the execution boundary.

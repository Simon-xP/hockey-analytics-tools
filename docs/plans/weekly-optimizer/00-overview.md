# Weekly Optimizer Rebuild: Overview

Read this before starting any package plan in this directory.
It fixes the vocabulary, the objective function, and the boundaries between packages.
Every other plan in this folder assumes you have read it.

Source of intent: `docs/weekly-optimizer.md`.
That document is the product spec written by the league owner.
When this plan and that document disagree, that document wins and you should flag the conflict rather than resolve it silently.

## What we are building

The decision engine that decides which fantasy transactions to make, and on which day, for the rest of a fantasy week.

The output is a **dated plan**:

```
Mon Jan 12   DROP Player A   ADD Player B      (B plays Mon/Wed/Sat)
Thu Jan 15   DROP Player B   ADD Player C      (C plays Thu/Fri/Sun)
             alternates for slot 2: Player D, Player E
```

Not a ranked list of swaps.
A calendar, with chains (a player added Monday can be the drop on Thursday), ranked alternates per move, and a conviction score that says which moves fire today and which stay pending.

## The objective, in three layers

This is the single most important thing to internalize.
The layers are independent and live in different packages.

```
1. POSTURE      Do we contest this week at all?
                CONTEST -> window is [as_of, this Sunday]
                PUNT    -> window is [next Monday, next Sunday]
                Also emits: how deep into our own roster we may reach.
                                                          package P5

2. OBJECTIVE    Maximize DELTA P(win) over the window.
                Not expected fantasy points.
                Ties broken by rest-of-season value.
                                                          package P6

3. CONSTRAINT   No move may drop a player whose hold value exceeds
                the floor. Hard constraint, not a penalty.
                Aggression sets the floor and nothing else.
                                                          package P4
```

### Why P(win) and not expected points

Because the correct move depends on whether you are ahead or behind, and expected points cannot express that.
Trailing by 40 with two days left, a volatile high-ceiling player is worth more than a steady one with the same mean.
Leading by 40, the reverse.
Under a `DELTA P(win)` objective this falls out of the normal-CDF math with no special cases and no tuned weights.

It also handles the calendar without any calendar logic.
Sensitivity of `P(win)` to a change in projected mean is `phi(gap / sigma) / sigma`.
Read that carefully, because it is easy to get backwards.

That quantity does **not** decay as the week runs out.
It decays as the matchup becomes *decided*.
A short window with a near-zero gap is the most sensitive state in the whole season: sigma is tiny, so the CDF is steep exactly where you are standing.

Worked example, tied matchup, default league scoring:

| | Player-games left | Net mean gain | Combined sigma | `DELTA P(win)` |
|---|---|---|---|---|
| Monday | ~25 | +6.0 | ~9.5 | ~0.25 |
| Sunday | ~4 | +2.0 | ~3.8 | ~0.21 |

A one-day Sunday stream in a close matchup is worth roughly as much as a full-week Monday add.
The model must never suppress it.
Monday moves do edge out Sunday moves in a tied matchup, scaling as `sqrt(days_remaining)`, but the margin is modest and it disappears entirely once the matchup is close enough.

### What this kills

Delete these on the way through.
They exist only because expected points was the objective.

| Symbol | File | Why it dies |
|---|---|---|
| `AGGRESSION_WEIGHTS` | `src/optimize/models/plan.py` | Aggression no longer weights anything. It sets a floor. |
| `score_transaction()` | `src/optimize/week/heavy.py` | Pairwise player-vs-player scoring is replaced by grid-vs-grid. |
| `build_candidates()` | `src/optimize/week/heavy.py` | Cartesian product of adds and drops, superseded by beam search. |
| `_apply_pool_relative_scaling()` | `src/optimize/week/heavy.py` | Mutates candidates in place, quantizes signals to -1/0/+1. Replaced by hold value. |
| `_should_defer()` | `src/optimize/week/heavy.py` | Heuristic deferral, replaced by real option value. |
| `_compute_opportunity_cost_threshold()` | `src/optimize/week/heavy.py` | Percentile hack, replaced by conviction against option value. |
| `FIRE_THRESHOLDS`, `INJURY_PENALTY_MULT`, `decide()` | `src/optimize/daily.py` | Entire parallel decision engine. Superseded. |
| `RosterPlayerState`, `compute_roster_state()` | `src/optimize/roster_state.py` | Absorbed into `WeekState`. |

`src/optimize/daily.py` and `src/optimize/roster_state.py` are deleted entirely in P7.
Do not build on them.
Do not "improve" them.

## The sliding window

The grid is always exactly seven days.
Posture chooses where it starts.

```
CONTEST, Tuesday:   [Tue, Wed, Thu, Fri, Sat, Sun]        (partial, 6 days)
PUNT,    Tuesday:   [next Mon ... next Sun]               (full, 7 days)
```

In PUNT mode the current week is invisible to both sides of the ledger.
A player with three games left this week and none next week is correctly worth nothing.
This is the `PREPARE` semantics from `docs/aggression-level.md`, implemented as a window shift rather than a special code path.

### Terminal value

A seven-day window truncates value that continues past its edge.

Every candidate therefore carries a **terminal value**: a scalar projection of what they are worth over the seven days *after* the window closes.
This is a cheap scalar (games in the next window multiplied by projected FPTS per game), not a second day-by-day grid.

**Terminal value is a tiebreak and nothing more.**
It is the rest-of-season term from the objective, and it only decides between moves whose `DELTA P(win)` is within epsilon of each other.
It must never outvote a live `DELTA P(win)` difference.
Getting this wrong produces exactly the failure the owner called out: refusing a one-day Sunday stream in a tight matchup because the alternative helps next week.

### There is no Sunday rule

Say this out loud before writing any planner code.
**Nothing in this system branches on the day of the week.**

The intuition "Sunday adds are really next-week adds" is usually true, and it is an *emergent* result, not an encoded one:

- On Sunday there are few games left, so `|gap| / sigma` is usually large, so `P(win)` is usually extreme.
- Extreme `P(win)` flips posture to PUNT (P5).
- PUNT slides the window to next Monday through next Sunday.
- The add is now scored against next week's schedule because that is literally the window.

And when the matchup *is* close on Sunday, none of that fires.
Posture stays CONTEST, the window is `[Sunday, Sunday]`, `DELTA P(win)` is at its most sensitive, and a one-game streamer wins on the merits.
The odds of punting rise as the week runs out. The rule does not.

Add budget resets Sunday, so unspent adds expire and the option value of holding one goes to zero at the boundary.
That changes the *bar* for firing (anything net-positive clears it), not *what* you spend the add on.
Posture decides what you spend it on.

## Package map

Eight packages.
`P0` must land before anything else.
Arrows are hard dependencies.

```
P0 contract
   |
   +-- P1 substrate --+-- P2 skater supply --+
   |                  |                      |
   +-- P5 posture     +-- P3 goalies --------+-- P6 planner -- P7 lifecycle
                      |                      |
                      +-- P4 hold value -----+
```

| | Package | Plan | Owns |
|---|---|---|---|
| P0 | Contract | `01-contract.md` | All shared dataclasses and protocols. No logic. |
| P1 | Substrate | `02-substrate.md` | `WeekState`, `LineupGrid`, empirical variance model. |
| P2 | Skater supply | `03-skater-supply.md` | Team day-pattern prescan, add candidate generation. |
| P3 | Goalies | `04-goalies.md` | Start prediction, crease share, goalie candidates. |
| P4 | Hold value | `05-hold-value.md` | Replacement level, hold cost, the drop floor. |
| P5 | Posture | `06-posture.md` | P(win), week importance, contest vs punt, floor level. |
| P6 | Planner | `07-planner.md` | Beam search over dated sequences, scoring, conviction. |
| P7 | Lifecycle | `08-lifecycle.md` | Replan triggers, plan persistence, diffing, execution, legacy deletion. |

### Suggested launch order

1. **P0 alone.** Everything else codes against these types. Do not parallelize this.
2. **P1 and P5 together.** Neither depends on the other.
3. **P2, P3, P4 together.** All three depend only on P0 and P1.
4. **P6 alone.** Needs all of the above.
5. **P7 alone.** Needs P6.

## Rules for parallel agents

Several of these packages will be built simultaneously by separate agents.
Follow these or integration will fail.

**Do not edit files owned by another package.**
Each plan lists the files it owns.
If you need a change to a type in P0, stop and report it rather than editing `src/optimize/models/week.py` yourself.

**Do not invent shared types.**
If the type you need is not in P0, it is either a package-private type (fine, keep it in your module) or a contract gap (report it).

**Code against the protocols, not the implementations.**
P2 must not import from P1's internals.
It takes a `LineupGrid` and calls its public methods.

**Respect `as_of` everywhere.**
Every query takes an `as_of` date and uses strict `<` comparison against `Game.date`.
This is not optional and it is not just for backtests.
`src/core/queries/stats.py` and `src/core/queries/schedule.py` exist specifically so live code and backtests share one leakage-safe path.
Leakage tests live in `tests/backtest/test_leakage.py`.

**Write scenario tests, not just unit tests.**
The spec is explicit that validation happens through hand-built scenarios the owner evaluates.
Every package plan lists named scenarios.
Implement them as tests with readable names and readable assertions, because a human is going to read the output and judge it.

**Do not touch `src/predict/`.**
Forecasts are a black box behind `forecast_player(session, nhl_id, game_date, as_of=...) -> dict`.
If a projection looks wrong, report it. Do not fix it here.

## Where the code lands

```
src/optimize/models/
    week.py           NEW   P0. All shared types for the weekly optimizer.
    plan.py           EDIT  P7. Strip AGGRESSION_WEIGHTS and the old WeekPlan.

src/optimize/week/
    __init__.py       EDIT  P6. optimize_week() dispatch, unchanged signature.
    state.py          NEW   P1. WeekState assembly.
    lineup.py         NEW   P1. LineupGrid.
    variance.py       EDIT  P1. Empirical CV replaces the 0.45 constant.
    supply.py         NEW   P2. Prescan and skater candidates.
    goalies.py        NEW   P3. Goalie candidates. Absorbs src/optimize/goalies.py.
    hold.py           NEW   P4. Hold value and floor. Absorbs drops.py, replacement.py.
    generate.py       NEW   P6. Beam search.
    score.py          NEW   P6. Plan scoring.
    select.py         NEW   P6. Conviction and fire dates.
    lifecycle.py      NEW   P7. Replan, diff, persistence.
    light.py          KEEP  Opponent projection. P5 and P6 consume it.
    heavy.py          DELETE P7, after P6 replaces it.

src/optimize/
    daily.py          DELETE P7.
    roster_state.py   DELETE P7.
    drops.py          DELETE P7, after P4 absorbs it.
    replacement.py    DELETE P7, after P4 absorbs it.
    goalies.py        DELETE P7, after P3 absorbs it.
    value.py          SHRINK P7. Schedule helpers survive, valuation moves to the grid.
    slots.py          KEEP  Bipartite slot assignment. P1 depends on it.
    injuries.py       KEEP  P1 and P4 depend on it.
    sync.py           KEEP  Valuation materialization.
    matchup/          EDIT  P5.
```

## Known problems to fix on the way through

These are real and already documented.
The package that touches the code owns the fix.

- **`src/optimize/goalies.py` leaks time.** `compute_goalie_game_log`, `compute_crease_share`, and `compute_opponent_softness` filter by `game_id` range but never by date, so they see the whole season regardless of `as_of`. Owned by P3.
- **`slots.py::assign_players_to_slots` dedupes by `player.name`, not `nhl_id`.** Two players with the same name collide. It is also greedy, not true bipartite matching, despite the docstring. Owned by P1.
- **`drops.py::rank_drops` reads `PlayerValuation` with no `as_of` filter** and falls back to on-demand computation with a warning. The stored valuation has no notion of when it was computed. Owned by P4.
- **`value.py::get_teams_playing_on_date` opens its own DB session per call**, inside a loop, per player, per game. This is the main reason the heavy path is slow. Owned by P1.
- **`CV = 0.45` in `variance.py` is a guess.** Under a P(win) objective it is load-bearing. Owned by P1.

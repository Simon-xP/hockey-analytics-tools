# P1: Substrate (WeekState, LineupGrid, Variance)

**Owns:** `src/optimize/week/state.py`, `src/optimize/week/lineup.py`, `src/optimize/week/variance.py`, `src/optimize/slots.py`

**Depends on:** P0

**Blocks:** P2, P3, P4, P6

Read `00-overview.md` and `01-contract.md` first.

## Purpose

This is the measuring instrument.
Every other package asks it the same question in different words: **what does my active lineup produce over this window, as a distribution, and how does that change if I make this move?**

Get this right and the planner is arithmetic.
Get it wrong and nothing above it can be correct.

Three deliverables:

1. `WeekState` assembly. One immutable snapshot, one `as_of`, everything downstream reads from it.
2. `LineupGrid`. Day-by-day slot assignment producing `(mu, sigma)` and supporting cheap what-if moves.
3. An empirical variance model to replace the `CV = 0.45` guess.

## Part 1: WeekState

`src/optimize/week/state.py`, one public function:

```python
def build_week_state(
    session: Session,
    league_key: str,
    my_team_key: str,
    as_of: date,
    week_start: date,
    week_end: date,
) -> WeekState
```

### Sources

| Field | Source | Notes |
|---|---|---|
| roster | `TeamRoster` table via `get_team_roster_nhl_ids` in `week/light.py` | Move that helper here; `light.py` should import it, not own it. |
| positions | `Player.yahoo_positions` | Fall back to `Player.position` mapped through `{C:C, L:LW, R:RW, D:D, G:G}`. This mapping is duplicated in five files today. Define it once here. |
| injuries | `src/optimize/injuries.py::load_injuries` | Already `as_of`-gated on `PlayerInjury.scraped_at`. Reuse as is. |
| earned scores | `matchup/scoreboard.py::build_matchup_snapshot_from_db` | Already gated on `Game.date < as_of`. |
| league settings | Yahoo league settings endpoint | **Do not hardcode `adds_per_week=4`.** If the endpoint is unavailable off-season, default to 4 and log that it was defaulted. |
| `ir_eligible` | Yahoo player status | A player is IR-eligible when Yahoo reports IR or IR+ status and an IR slot is open. |
| `is_protected` | config | A user-editable list of nhl_ids. Put it in `config/settings.py`. |

### The projection cache

This is the performance-critical design decision in the whole rebuild.

The planner will build thousands of candidate grids.
If each one calls `forecast_player()`, it will take hours.

So: **`build_week_state` resolves every projection it will ever need, once, up front.**

```python
@dataclass(frozen=True)
class ProjectionCache:
    values: Mapping[tuple[int, date], float]   # (nhl_id, game_date) -> expected FPTS
    def get(self, nhl_id: int, day: date) -> float | None: ...
```

Populate it for the union of (roster players, candidate pool) over (window days, plus the seven terminal-value days after `window_end`).
Attach it to `WeekState`.

After this point, no module below the planner touches the database or `src/predict/`.
The grid is pure arithmetic over a dict.

Load forecast dependencies once, the way `value.py::_get_forecast_deps` does, and pass them through.
Do not reimplement that caching, and do not use the module-level `_FORECAST_CACHE` global, which is not `as_of`-aware and will hand a backtest the wrong models.

### Fix: `get_teams_playing_on_date`

`src/optimize/value.py::get_teams_playing_on_date` opens a **new database session per call**, and it is called inside a loop over players inside a loop over games.
It also queries `Team` twice per game.

Replace it with one query at state-build time that produces `Mapping[date, frozenset[str]]` for the whole window, stored on `WeekState`.

## Part 2: LineupGrid

`src/optimize/week/lineup.py`.

```python
def build_grid(state: WeekState, window_start: date, window_end: date) -> LineupGrid
```

For each day in the window:

1. Which rostered players' NHL teams play that day (from the state's schedule map).
2. Assign them to active slots, **maximizing projected FPTS**.
3. `mu` is the sum of starter projections. `var` comes from the variance model in Part 3.
4. Record `open_slots` so P2 knows which day-patterns are worth anything.

### Fix: slot assignment must be optimal and must use projections

`src/optimize/slots.py::assign_players_to_slots` has two defects that matter here.

**It ignores projections entirely.**
It fills slots by positional scarcity, so it can bench a 6-FPTS player to start a 2-FPTS one.
For a grid whose whole job is to compute expected points, that is disqualifying.

**It is greedy, not bipartite, despite the docstring.**
Single-position players get first pick, then multi-position players take what is left.
That is not optimal and it is easy to construct a counterexample.

Replace it with a real max-weight assignment.
Build a matrix of players against **slot instances** (a league with 4 D slots yields 4 columns), weight each cell by the player's projected FPTS for that day, forbid ineligible cells, and solve with `scipy.optimize.linear_sum_assignment`.
`scipy` is already a dependency; `matchup/win_probability.py` imports it.

Sizes are tiny (roughly 20 players by 13 slots), so this is microseconds and can run thousands of times.

**Also fix:** the current implementation dedupes with `assigned_players: set[str]` keyed on `player.name`.
Two players sharing a name collide and one silently vanishes.
Key on `nhl_id`.

Keep the existing function signature working, because `src/api/routers/yahoo.py` calls it.
Add an optional `projections: Mapping[int, float] | None` parameter that switches on the weighted path, and leave the unweighted behavior as the default so the API route is unaffected.

### `with_move`

```python
def with_move(self, add: Candidate | None, drop: int | None, on_date: date) -> LineupGrid
```

Days before `on_date` are copied unchanged.
Days from `on_date` forward are recomputed with the modified roster.

Two rules that are easy to get wrong:

- **A move on day D affects day D.** Yahoo transactions process immediately, so a Monday-morning add plays Monday night.
- **`add=None, drop=X`** is a legal pure drop, and **`drop=None`** is a free add against an open active slot or a freed IR slot. Both occur in real plans.

Cache aggressively.
Recomputing only the affected suffix is the difference between a beam search that finishes and one that does not.

### Terminal value

```python
def terminal_value(state: WeekState, nhl_id: int, window_end: date) -> float
```

Expected FPTS over `[window_end + 1 day, window_end + 7 days]`, using the same projection cache.

This is a scalar, not a grid.
Do not slot-check it.
It is a tiebreak term (see `00-overview.md`) and precision beyond one significant figure is wasted.

## Part 3: The variance model

`src/optimize/week/variance.py` currently holds `CV = 0.45` with a comment calling it "the working estimate."
Under a `P(win)` objective this constant *is* the risk behavior. Measure it.

### What to measure

Not the raw variance of game FPTS.
We need the **predictive** variance: how far actual outcomes land from what our model projected.
That folds in both true game-to-game noise and our own projection error, which is exactly the uncertainty `P(win)` should reflect.

Procedure:

1. Use the walk-forward harness in `src/predict/forecasting/evaluation.py` to generate out-of-sample per-game projections across a full season.
2. Join to actuals in `GameIndividualStats.fpts`.
3. Bucket by projected FPTS. Within each bucket compute `std(actual - projected)`.
4. Fit `sigma(mu)`. Test at minimum a constant CV (`sigma = k * mu`), an affine form (`sigma = a + b * mu`), and a power form.

Expect the affine form to win.
A player projected for 0.5 FPTS does not have a 0.22 standard deviation; peripherals alone produce more scatter than that.
A pure CV model understates the noise on low-projection players, which is precisely the streaming pool.

Ship whichever form fits, expose it as a function, and record the fitted coefficients and the season they came from in a module docstring.

### Correlation

Independence is assumed today: `compute_team_sigma` sums variances.
Linemates are not independent, because a goal usually credits two or three of them.

Measure it: for pairs of forwards on the same team, correlate residuals on shared game dates.
If the average correlation is above roughly 0.15, add a pairwise correction term for same-team players in the same grid.
If it is below that, document the measurement and keep the independent sum.

This is a stretch goal.
Do the univariate fit first and ship it.

### Keep the interface

The signature is pinned in `01-contract.md` section 8 and you may not change it:

```python
def game_sigma(projected_fpts: float, player_type: PlayerType) -> float
def team_sigma(per_game_fpts: Sequence[float]) -> float
```

You own the function and the skater branch.
P3 is being built in parallel and owns the goalie coefficients, which it will supply as fitted constants for `game_sigma` to dispatch into.
Leave that branch stubbed (fall back to the skater curve with a `TODO` referencing P3) rather than blocking on it.

`compute_team_sigma` is imported by `week/light.py` and `tests/optimize/matchup/test_win_probability.py`.
Keep that name as an alias.

## Acceptance scenarios

Write these as named tests in `tests/optimize/week/test_lineup.py` and `test_state.py`.
A human is going to read the output, so make the assertions readable.

**Grid correctness**

- `slot_blocked_player_scores_zero`: a roster with 3 healthy centers and 2 C slots on a day all three play. The lowest-projected center is benched and contributes nothing to `mu`.
- `multi_position_player_fills_the_scarce_slot`: a C/LW on a day with a full C slot and an open LW slot starts at LW.
- `util_absorbs_the_overflow`: with all named slots full and UTIL open, the highest-projected leftover skater takes UTIL, not an arbitrary one.
- `assignment_beats_greedy`: a hand-built case where the current greedy code benches a high-projection player. Assert the new assignment scores strictly higher.
- `duplicate_names_do_not_collide`: two `RosterEntry` rows with identical `name` and different `nhl_id` both get assigned.

**with_move**

- `move_applies_same_day`: adding on Wednesday changes Wednesday's `mu`.
- `move_does_not_touch_the_past`: days before `on_date` are byte-identical to the base grid.
- `free_add_needs_no_drop`: `drop=None` with an open active spot raises nothing and increases `mu`.
- `chained_move`: add A on Monday, then drop A for B on Thursday. Monday through Wednesday reflect A, Thursday onward reflect B.

**Leakage**

- `grid_ignores_games_after_as_of_for_stats`: build two states with different `as_of` over the same window and assert the earlier one cannot see the later one's results. Follow the pattern in `tests/optimize/test_value.py`.

**Variance**

- `sigma_is_monotonic_in_mu`.
- `sigma_of_empty_window_is_zero`.
- `calibration_check`: on held-out data, the fraction of actual weekly totals falling inside the model's 80% interval is within 0.75 to 0.85. This is the test that says the variance model is trustworthy. If it fails, the P(win) objective is not safe to ship and you should say so loudly rather than tune until it passes.

## Do not

- Do not import from `src/optimize/week/supply.py`, `hold.py`, `generate.py`, or `score.py`. They depend on you, not the reverse.
- Do not add scoring, ranking, or any notion of a "good" player. The grid measures; it does not judge.
- Do not delete `src/optimize/value.py`. P7 shrinks it. You may move `get_teams_playing_on_date` and add a deprecation shim.
- Do not change `forecast_player`'s signature or anything under `src/predict/`.

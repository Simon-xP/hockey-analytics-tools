# P0: Contract

**Owns:** `src/optimize/models/week.py` (new file, this package's only deliverable)

**Depends on:** nothing

**Blocks:** every other package

Read `00-overview.md` first.

## Purpose

Every other package in this rebuild is built by a separate agent, in parallel.
This package defines the types they all speak.
If two agents invent their own `AddCandidate`, integration fails and the work is wasted.

This package contains **no logic**.
Dataclasses, enums, and protocols only.
No database access, no imports from `src/optimize/week/`, no computation beyond trivial derived properties.

## Design rules

**Frozen dataclasses by default.**
`WeekState` and everything reachable from it is an immutable snapshot taken at a single `as_of`.
Mutation is how time leakage gets in.
The one deliberate exception is documented per type below.

**Tuples, not lists, in frozen types.**
A frozen dataclass holding a `list` is not actually immutable.
Use `tuple[...]` for sequences and `Mapping[...]` for dicts on frozen types.

**No optional fields that hide missing data.**
If a projection could not be computed, the candidate should not exist.
Do not default a projection to `0.0` and let a downstream package guess whether that means "zero points" or "unknown."

**Every type carries its reasoning.**
The spec wants an agent monitor UI that explains decisions.
Anything the planner produces carries a `reasoning: tuple[str, ...]` that a human can read.
Write reasoning strings for a fantasy manager, not for a debugger.

## Types to define

Group them in the file with section comments in this order.

### 1. Posture

Output of P5.
Read by P4 (floor), P1 (window), P6 (objective selection).

```python
class PostureMode(Enum):
    CONTEST = "contest"   # maximize P(win) for the current matchup
    PUNT    = "punt"      # current week is decided, optimize next week
```

`AggressionLevel` already exists in `src/optimize/models/plan.py` with five members.
Re-export the four intensity members from `week.py`.

`PREPARE` is dead. It is now `PostureMode.PUNT`, which is a different axis: mode and depth are orthogonal, so you can punt conservatively or punt aggressively.

**Do not delete `PREPARE` from the enum.**
`src/backtest/`, `scripts/run_backtest.py`, and `tests/optimize/matchup/test_state_engine.py` all reference it, and removing it here turns the test suite red for every agent working in parallel behind you.
Add a comment marking it deprecated, and let P7 remove it as part of the cleanup pass.
No package between here and P7 may emit it.

```python
@dataclass(frozen=True)
class Posture:
    mode: PostureMode
    window_start: date          # inclusive
    window_end: date            # inclusive
    aggression: AggressionLevel # sets the drop floor, nothing else
    p_win: float                # P(win) of the CURRENT matchup, always computed
    importance: WeekImportance  # reuse from models/matchup.py
    reasoning: tuple[str, ...]
```

`p_win` is populated even in PUNT mode.
The UI needs to show why we gave up.

### 2. League and roster state

```python
@dataclass(frozen=True)
class LeagueSettings:
    league_key: str
    slots: RosterSlotSettings   # reuse from models/roster.py
    n_teams: int
    adds_per_week: int          # read from Yahoo settings, do not hardcode 4
    waiver_days: int
    roster_size: int

@dataclass(frozen=True)
class RosterEntry:
    nhl_id: int
    name: str
    team_abbrev: str
    positions: tuple[str, ...]      # Yahoo eligibility
    injury_status: str | None       # "IR", "IR+", "OUT", "DTD", None
    expected_return: date | None
    ir_eligible: bool               # can be moved to an IR slot right now
    is_protected: bool              # user-marked never-drop
```

```python
@dataclass(frozen=True)
class WeekState:
    as_of: date
    week_start: date
    week_end: date
    league: LeagueSettings
    my_team_key: str
    opp_team_key: str
    roster: tuple[RosterEntry, ...]
    my_earned: float
    opp_earned: float
    adds_remaining: int
    opp_adds_remaining: int
    open_active_spots: int      # roster_size minus non-IR players
    open_ir_spots: int
```

### 3. Lineup grid

`DaySlate` is concrete.
`LineupGrid` is a `Protocol` so P2, P4, and P6 can type-hint against it without importing P1.
P1 provides the implementation.

```python
@dataclass(frozen=True)
class DaySlate:
    day: date
    starters: tuple[int, ...]        # nhl_ids assigned to active slots
    benched: tuple[int, ...]         # playing but slot-blocked
    open_slots: Mapping[str, int]    # "C" -> 1, "D" -> 0, ...
    mu: float                        # sum of starter projections
    var: float                       # sum of starter variances
```

```python
class LineupGrid(Protocol):
    window_start: date
    window_end: date
    days: tuple[DaySlate, ...]

    def mu(self) -> float: ...
    def sigma(self) -> float: ...

    def with_move(
        self, add: "Candidate | None", drop: int | None, on_date: date
    ) -> "LineupGrid": ...
    """Return a new grid with the move applied from on_date forward.

    add=None means a pure drop. drop=None means a free add (open slot or IR).
    Days before on_date are unchanged.
    """

    def marginal(self, candidate: "Candidate", on_date: date) -> tuple[float, float]: ...
    """(delta_mu, delta_var) from inserting candidate at on_date, no drop."""
```

`with_move` returns a new grid.
Never mutate.
The planner explores thousands of branches and shared mutable state will corrupt them.

### 4. Candidates

One type for skaters and goalies.
The spec is explicit that they compete in the same pool.

```python
@dataclass(frozen=True)
class Candidate:
    nhl_id: int
    name: str
    team_abbrev: str
    positions: tuple[str, ...]
    player_type: PlayerType              # reuse from models/value.py
    projections: Mapping[date, float]    # expected FPTS per day IN WINDOW
    terminal_value: float                # expected FPTS over the 7 days AFTER window_end
    available_from: date | None          # waiver clear date, None = available now
    upside_score: float                  # [-1, 1]
    opportunity_score: float             # [-1, 1]
    confidence: float                    # [0, 1], how sure are the projections
    source: str                          # "fa", "waiver", "goalie_stream"
```

`projections` holds **expected** FPTS.
For a goalie with a 60% chance of starting, that is `0.6 * start_value`, already multiplied out.
Days the player does not play are absent from the mapping, not present with a zero.

`confidence` is separate from the expectation and feeds the conviction decision in P6.
A confirmed starter and a coin-flip starter can have identical `projections` and very different `confidence`.

Variance is **not** a candidate field.
The grid derives variance from mu via the CV model in P1, so there is one variance model in the system.

### 5. Hold value and the floor

```python
@dataclass(frozen=True)
class HoldValue:
    nhl_id: int
    ros_over_replacement: float   # dominant term
    window_contribution: float    # minor term
    upside_option: float
    scarcity: float
    total: float
    protected: bool               # hard never-drop, overrides everything
    reasoning: tuple[str, ...]

@dataclass(frozen=True)
class DropFloor:
    aggression: AggressionLevel
    threshold: float
    def permits(self, hold: HoldValue) -> bool: ...
```

`permits` is the only method with logic in this file and it is one line: `not hold.protected and hold.total <= self.threshold`.
It lives here because it is the contract between P4 and P6.

### 6. Plan

```python
@dataclass(frozen=True)
class PlannedMove:
    fire_date: date
    add: Candidate
    drop: RosterEntry | None      # None = free add via open slot or IR
    is_ir_move: bool              # move an injured player to IR, no drop
    delta_p_win: float
    delta_terminal: float
    alternates: tuple[Candidate, ...]   # ranked backups for this same slot
    reasoning: tuple[str, ...]

@dataclass(frozen=True)
class WeekPlan:
    plan_id: str
    generated_at: datetime
    state_fingerprint: str        # hash of the WeekState that produced this
    posture: Posture
    moves: tuple[PlannedMove, ...]
    baseline_p_win: float         # P(win) if we do nothing
    projected_p_win: float        # P(win) if we execute every move
    adds_used: int
    conviction: float             # [0, 1], how sure we are in move[0]
    fire_now: tuple[int, ...]     # indices into moves to execute this run
    reasoning: tuple[str, ...]

@dataclass(frozen=True)
class PlanSet:
    best: WeekPlan
    alternates: tuple[WeekPlan, ...]   # whole-plan alternatives, ranked
```

Two levels of alternate, deliberately.
`PlannedMove.alternates` answers "someone took my target, who else fits this slot."
`PlanSet.alternates` answers "what is the second-best shape for the whole week."
The spec asks for both.

`state_fingerprint` is what P7 uses to decide whether a plan is stale.
Define it here as a `str`; P1 decides how to compute it from `WeekState`.

### 7. Protocols

```python
class ProjectionProvider(Protocol):
    def project(self, nhl_id: int, game_date: date) -> float: ...

class CandidateSource(Protocol):
    def candidates(
        self, state: WeekState, grid: LineupGrid, posture: Posture
    ) -> list[Candidate]: ...
```

### 8. The variance signature

Pinned here, and **not** negotiable by the packages that implement it, because P1 (skater variance) and P3 (goalie variance) are built in parallel and cannot agree on anything mid-flight.

```python
def game_sigma(projected_fpts: float, player_type: PlayerType) -> float: ...
```

P1 owns the function and the skater branch.
P3 owns the goalie coefficients and hands them to P1's module as fitted constants.
Neither may change the signature.

A goalie's variance has a second component that a skater's does not (start uncertainty is a Bernoulli term on top of outcome noise), so P3 additionally owns:

```python
def goalie_game_var(p_start: float, start_value: float, outcome_var: float) -> float: ...
```

The grid calls `game_sigma`; `game_sigma` dispatches on `player_type` into the goalie path when needed.

`ProjectionProvider` exists so P1 can be tested with a fake and so the whole system never imports `src/predict/` directly outside one adapter.
`CandidateSource` is what P2 and P3 each implement, so P6 consumes one uniform pool.

## Acceptance

- `src/optimize/models/week.py` imports cleanly with no dependency on any module under `src/optimize/week/`.
- **`pytest` is still green after this package lands.** It is the last point in the rebuild where that is true, so verify it. If it is red, you deleted something you should not have.
- Every frozen dataclass survives `dataclasses.replace()` and is hashable where it needs to be.
- `mypy` or equivalent passes on the module in isolation.
- One test file, `tests/optimize/models/test_week_contract.py`, that constructs a fully-populated instance of every type. It exists to break loudly when a downstream package silently changes a field.

## Do not

- Do not add a `to_dict()` or any serialization. P7 owns persistence and will decide the shape.
- Do not add computed properties that hide work. `WeekPlan.projected_p_win` is a stored field set by P6, not a property that recomputes.
- Do not delete or edit `src/optimize/models/plan.py` beyond removing `PREPARE`. P7 handles the rest of that file.

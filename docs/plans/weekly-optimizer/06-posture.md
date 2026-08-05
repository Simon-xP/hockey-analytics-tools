# P5: Posture (contest vs punt, window, floor level)

**Owns:** `src/optimize/matchup/` (all of it), `src/optimize/week/posture.py`

**Depends on:** P0

**Blocks:** P4 (needs the floor level), P6 (needs the window and mode)

Read `00-overview.md` and `01-contract.md` first.

## Purpose

One question, answered once per planning run, feeding everything else:

**Are we trying to win this week, and if so, how far into our own roster are we allowed to reach?**

Output is a single `Posture` object.
It is small, it is pure, and it is the highest-leverage twenty lines in the system.
A wrong posture makes every downstream decision wrong in a way no amount of search quality can recover.

## What already exists

More than you might expect.
`src/optimize/matchup/` is largely built and roughly correct.

| File | State |
|---|---|
| `win_probability.py` | Works. Normal-CDF over both teams' `(mu, sigma)` plus pickup boosts. Keep. |
| `state_engine.py` | Works. Maps P(win) plus `WeekImportance` to an `AggressionLevel`. Needs reshaping, not rewriting. |
| `scoreboard.py` | Works. Live Yahoo fetch and a DB reconstruction for backtests. Keep. |
| `__init__.py` | Composes the above. Needs to emit `Posture` instead of `AggressionLevel`. |

Your job is mostly to **split one output into three** and calibrate the thresholds.

## The reshape

Today `determine_aggression()` returns a single five-valued enum where `PREPARE` is smuggled in alongside four intensity levels.
That conflates two independent axes.

Split them:

```
p_win + importance  -->  mode        CONTEST | PUNT
                    -->  window      derived from mode
                    -->  aggression  CONSERVATIVE | NORMAL | AGGRESSIVE | DESPERATE
```

### Mode

```
PUNT when the matchup is decided in either direction:
    p_win > punt_high    (we have already won, protect and prepare)
    p_win < punt_low     (we have already lost, stop burning assets)

CONTEST otherwise.

WeekImportance.CRAZY disables PUNT entirely. Playoffs are never conceded.
```

Current thresholds are `0.95` and `0.05`.
They are guesses. Calibrate them (see below).

### Window

Purely derived, no independent logic:

```
CONTEST:  (as_of, week_end)
PUNT:     (next_monday, next_sunday)   where next_monday = week_end + 1 day
```

`week_end` comes from the Yahoo matchup, not from `as_of.weekday()`.
Fantasy weeks do not always run Monday to Sunday; the All-Star break and the season's first and last weeks are irregular.

### Aggression

The four intensity levels survive, with **one job only**: setting the drop floor in P4.
They no longer weight anything.

```
p_win > 0.85    CONSERVATIVE   we are winning, do not churn the roster
0.55 to 0.85    NORMAL
0.25 to 0.55    AGGRESSIVE
below 0.25      DESPERATE      reach deep, accept roster damage

WeekImportance.NEUTRAL caps at AGGRESSIVE. A meaningless week is never
worth damaging the roster for.
```

In PUNT mode, aggression still matters, because you still have to decide what you are willing to drop to improve next week.
Default PUNT to CONSERVATIVE and let the calibration scenarios tell you whether that is right.
It is orthogonal to mode by design; do not collapse them back together.

## The hard part: what a win is worth

`punt_high` and `punt_low` encode a judgment the code cannot derive: how much season-long roster value is one matchup win worth?

**Build the threshold version now.**
Put it behind an interface so a standings simulator can replace it later without touching P6.

```python
class WinValuation(Protocol):
    def punt_bounds(self, state: WeekState, importance: WeekImportance) -> tuple[float, float]:
        """Return (punt_low, punt_high) for this matchup."""
```

Ship `ThresholdWinValuation`, which returns fixed bounds keyed by importance tier.
Document `SimulatedWinValuation` in the module docstring as the intended successor: simulate the remaining schedule, compute P(playoffs) with and without this win, and set the bounds where the marginal playoff probability stops justifying roster damage.
Do not build it.

## Week importance

`auto_importance()` exists and is crude:

```python
on_bubble = my_rank > (playoff_spots - 2)
```

Two known gaps, both already tracked as wanted work:

- **Playoff byes.** In a league where the top seeds get a bye, finishing 1st is materially better than finishing 4th, so a week that only moves you within the playoff field can still matter a lot. Rank alone cannot see that.
- **Games in hand and strength of remaining schedule.** Rank on its own is a weak proxy for playoff probability in mid-season.

Improve `auto_importance()` to take `weeks_remaining` and the standings gap in points, not just rank.
A team three points out with six weeks left is in a very different position from one three points out with one week left, and today they get the same tier.

Leave the manual override in place.
The owner will want to force CRAZY on a specific week.

## Calibration

This is the deliverable that actually matters, and the spec is explicit that it happens through hand-built scenarios the owner judges.

Build `tests/optimize/matchup/test_posture_scenarios.py` as a table-driven test.
Each row is a realistic matchup state and the posture a strong human manager would choose.

Seed it with these.
The expected values are the owner's to confirm or correct, so print actual against expected on failure in a readable format rather than a bare assertion diff.

| Day | Gap | My games left | Opp games left | Importance | Expected |
|---|---|---|---|---|---|
| Monday | 0 | 25 | 25 | BIG | CONTEST / NORMAL |
| Monday | -50 | 25 | 25 | BIG | CONTEST / AGGRESSIVE (high variance, very winnable) |
| Saturday | -50 | 4 | 4 | BIG | PUNT (decided) |
| Saturday | -50 | 4 | 4 | CRAZY | CONTEST / DESPERATE (playoffs, never concede) |
| Sunday | -6 | 3 | 2 | BIG | CONTEST / DESPERATE (very close, maximum leverage) |
| Sunday | -6 | 3 | 2 | NEUTRAL | CONTEST / AGGRESSIVE (capped) |
| Sunday | +60 | 3 | 3 | BIG | PUNT (won) |
| Sunday | +60 | 3 | 3 | CRAZY | CONTEST / CONSERVATIVE (do not risk it) |
| Wednesday | +15 | 14 | 18 | BIG | CONTEST / NORMAL (lead is thinner than it looks) |
| Thursday | 0 | 8 | 14 | BIG | CONTEST / AGGRESSIVE (schedule disadvantage) |

Row 5 is the one to watch.
It is the case the owner raised directly: a close matchup on Sunday must stay CONTEST so a one-day stream is reachable.
If the thresholds punt that row, they are wrong.

Rows 9 and 10 exist to confirm that games-remaining asymmetry is doing real work.
Gap alone is not the signal; gap relative to combined sigma is.

## Acceptance

- `Posture` is the only thing `src/optimize/matchup/__init__.py` returns publicly.
- `PREPARE` no longer appears anywhere in the codebase.
- Every scenario row passes, or is explicitly marked `xfail` with a comment saying the owner has not ruled on it yet.
- `p_win` is populated in PUNT mode. The UI shows why we conceded.
- `determine_aggression_from_context()` stays available as a pure function. `src/backtest/` and the existing tests use it.
- Boundary behavior is tested: `p_win` exactly at each threshold, `sigma = 0` (no games left), and an empty roster.

## Do not

- Do not branch on `as_of.weekday()` anywhere. See the "There is no Sunday rule" section of `00-overview.md`.
- Do not build the standings simulator. Interface only.
- Do not touch `week/light.py` beyond calling it. P6 owns changes there.
- Do not reintroduce weights. If you find yourself wanting aggression to scale a score, the design has drifted and you should report it.

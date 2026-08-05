# P4: Hold Value and the Drop Floor

**Owns:** `src/optimize/week/hold.py` (new), absorbing `src/optimize/drops.py` and `src/optimize/replacement.py`

**Depends on:** P0, P1. Not P5 — `AggressionLevel` already exists and P0 re-exports it, so you can build the floor before posture is finished.

**Blocks:** P6

Read `00-overview.md` and `01-contract.md` first.

## Purpose

Answer one question for every player on the roster: **what does it cost us to lose this player, permanently, right now?**

Then turn that into a hard gate. Players whose hold value exceeds the floor are removed from the drop pool entirely and the planner never sees them.

This is the package that stops the agent from doing something stupid.
The spec spends two of its most emphatic paragraphs on it.

## The framing error to avoid

Anchoring hold value on **window contribution** is the obvious approach and it is wrong.

Consider an injured star.
He contributes nothing over the window, so a window-anchored hold value marks him as maximally droppable.
The agent drops Connor McDavid because he has a sprained wrist.
That is the single worst move the system could make and a naive implementation makes it eagerly.

Hold value must be dominated by **rest-of-season value over replacement**.
An injured star has near-zero window value and enormous ROS value, which is the correct answer.

The owner's escape hatch then falls out automatically rather than needing a rule:

> unless there are virtually no games left that they will play that will be impactful for the team

Late in the season, or with a season-ending injury, ROS value collapses too, and the drop becomes legal on its own.
No special case required.

## Before any of this: the IR path

An injured player is usually not a drop decision at all.

If the player is IR-eligible and an IR slot is open, the correct move is to **move them to IR and take a free add**.
No drop, no cost, an extra roster spot.
The spec calls this out as the standard response to an injury.

So the ordering is:

```
1. Injured and IR-eligible and IR slot open?   -> IR move, free add. Done.
2. Otherwise compute hold value.
3. Compare against the floor.
```

Expose the IR check from this module so P6 can branch on it before it starts searching drops.
Getting this wrong means the agent drops an injured star while an empty IR slot sits open, which is worse than the naive failure above.

## Part 1: Replacement level

`ReplacementLevel` from `src/optimize/models/value.py` survives as a type.
Its computation does not.

The current `compute_replacement_level` averages the FPTS per game of the top 5 free agents per position group.
Three problems:

- **`top_n = 5` is a magic constant.** The spec is explicit that the system must be league-agnostic, and a 5-team league and a 20-team league have wildly different wires.
- **No slot awareness.** A league with 4 D slots demands far more defensemen than one with 2.
- **`min_gp = 10` silently drops the entire early season** and every recent call-up, which is exactly the population you are streaming from.

### The replacement level to build

Two definitions, both defensible, and they should agree in a healthy league:

**Demand-derived.** Replacement at position `P` is roughly the `(n_teams * slots_at_P + 1)`th best player at `P` league-wide. This is standard VORP. It is stable and insensitive to a temporarily picked-over wire.

**Supply-observed.** The best actually-available free agent at `P` right now. This is what "can I re-acquire this production for free" literally means, and it is directly observable.

Use supply-observed as primary, because it answers the real question, but smooth it: average the top `K` available, where `K` derives from league size and slot counts rather than being fixed. Use demand-derived as a sanity bound and log when the two diverge by more than a modest margin, because a large divergence means the wire is unusually rich or unusually barren and that is itself useful signal.

**Handle UTIL.** In a league with UTIL slots, forwards and defensemen compete for the same roster spot, so position groups are not independent. At minimum, fold UTIL demand into both groups proportionally. Do not ignore it.

Per the spec, replacement level incorporates upside and opportunity, with everything else measured on projected fantasy points per game.

## Part 2: Hold value

```python
def compute_hold_value(
    state: WeekState, grid: LineupGrid, entry: RosterEntry,
    replacement: ReplacementLevel, posture: Posture,
) -> HoldValue
```

Four terms, in descending order of weight:

**1. ROS over replacement.** Dominant. Projected FPTS per game above the replacement rate at their position, multiplied by remaining games in the season. Injury-adjusted: an injured player's remaining games are reduced by the expected games missed from `src/optimize/injuries.py::estimate_games_missed`, but not to zero unless the injury is season-ending.

**2. Window contribution.** Minor. What the grid loses if this player disappears today: `grid.mu() - grid.with_move(None, entry.nhl_id, as_of).mu()`. Note this is naturally near zero for a bench-blocked player, which is correct here even though it would be wrong as the dominant term.

Compute against the **posture's** window. In PUNT mode this is next week, so a player with games this week and none next week correctly contributes nothing.

**3. Upside option value.** The spec's core anxiety: do not drop the player who is about to break out. `upside_score` is talent ceiling, which persists, so it belongs in hold value. `opportunity_score` is situational and temporary, so it belongs in the window term at most. Do not weight them equally; they are different things (see `docs/upside-and-opportunity.md`).

**4. Positional scarcity.** How hard is this player to replace on your own roster.

### Fix: positional scarcity over-fires

`compute_position_scarcity` in `drops.py` returns nonzero scarcity for a defenseman on a roster with four other D-eligible players, because the formula measures slot tightness rather than genuine irreplaceability.
This is noted as a known edge case in `docs/autonomous-agent.md`.

Rebuild it against the grid instead of against a count.
Scarcity is the difference in window `mu` between dropping this player and dropping a hypothetical identical-rate player at an abundant position.
If removing them costs no lineup flexibility, scarcity is zero, whatever the roster counts say.

### Fix: `rank_drops` reads stale valuations

`drops.py::rank_drops` calls `session.get(PlayerValuation, nhl_id)` with **no `as_of` filter**.
`PlayerValuation` has no notion of when it was computed, so a backtest reading it gets whatever the last nightly sync wrote, which is the future.

Do not read `PlayerValuation` in this package.
Read the projection cache on `WeekState`, which P1 built under a known `as_of`.

## Part 3: The floor

Hard constraint. The owner chose this deliberately over a soft penalty.

```python
def compute_floor(state, replacement, aggression) -> DropFloor
```

Aggression's only job in the entire system is setting this threshold.

```
CONSERVATIVE   only players clearly below replacement are droppable
NORMAL         players at or modestly above replacement
AGGRESSIVE     reach into useful contributors
DESPERATE      reach deep, but the protected list still holds
```

Express the threshold in the same units as `HoldValue.total` so `permits()` is a plain comparison.

`is_protected` is absolute.
No aggression level overrides a user-marked protected player.
DESPERATE is not a license to drop anyone.

The floor **excludes** players from the pool rather than penalizing them.
P6 never sees a player above the floor, so it cannot trade season value for a probability sliver.

## Acceptance scenarios

`tests/optimize/week/test_hold.py`.
Name them so a human reading the output can judge them.

**The injured star suite. This is the reason the package exists.**

- `does_not_drop_an_injured_star_in_november`: elite ROS value, zero window value, six weeks of season left. Not droppable at any aggression level.
- `ir_move_preempts_the_drop_decision`: same player, IR slot open. The module reports an IR move, and hold value is never consulted.
- `does_not_drop_an_injured_star_when_no_ir_slot_is_open`: the fallback still refuses. An open IR slot is not what protects him.
- `drops_a_season_ending_injury_in_the_final_week`: same player, season-ending, one week left. ROS has collapsed, so the drop is now legal. This is the owner's escape hatch firing on its own.
- `does_not_drop_a_healthy_star_on_a_one_game_week`: bad schedule is not a reason to drop a stud. A window-anchored implementation fails this one.

**Floor behavior**

- `floor_widens_monotonically_with_aggression`: the DESPERATE drop pool strictly contains the CONSERVATIVE pool at every level.
- `protected_player_is_never_droppable`, including at DESPERATE.
- `bench_blocked_scrub_is_droppable`: low ROS, zero window value, abundant position. Droppable at NORMAL.
- `high_upside_low_production_player_survives_normal`: the breakout candidate the spec worries about. Droppable at DESPERATE, not at NORMAL.

**Replacement level**

- `replacement_scales_with_league_size`: an 8-team and a 20-team league over the same player universe produce materially different replacement levels. If they do not, the computation is not league-agnostic and has not met the spec.
- `replacement_scales_with_slot_counts`: 2 D slots versus 6 D slots.
- `early_season_replacement_is_computable`: with every player at three games played, `min_gp = 10` must not empty the pool.

**Punt mode**

- `punt_mode_ignores_this_weeks_games_in_hold_value`: a player with four games this week and none next is, in PUNT mode, worth only his ROS.

**Leakage**

- `hold_value_respects_as_of`, following the pattern in `tests/optimize/test_replacement.py`.

## Do not

- Do not compute goalie replacement level. Goalie value is volume-driven; the spec and `ReplacementLevel`'s docstring both say so.
- Do not rank or select drops. You produce hold values and a floor. P6 searches.
- Do not make the floor soft, add a penalty band, or make `permits()` return a score. The owner chose a hard constraint and the predictability is the point.
- Do not delete `drops.py` or `replacement.py`. Land the replacement, leave the old files importable, and let P7 remove them once P6 has migrated.

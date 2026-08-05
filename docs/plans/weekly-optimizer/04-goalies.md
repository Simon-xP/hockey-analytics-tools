# P3: Goalies

**Owns:** `src/optimize/week/goalies.py` (new), absorbing and replacing `src/optimize/goalies.py`

**Depends on:** P0, P1, and P2's `Candidate` usage pattern

**Blocks:** P6

Read `00-overview.md` and `01-contract.md` first.

## Purpose

Produce goalie add candidates that compete in the same pool as skaters, and make sure the variance model treats them honestly.

The spec is direct about why goalies get special attention:

> we might want to weigh goalie streams higher than player streams for individual nights, because goalies typically get higher variation in their scores, but more consistent production than a skater

Under a `P(win)` objective you do **not** implement that as a weight.
Higher variance is a property the model should measure, and `P(win)` will value it correctly on its own: variance helps when behind and hurts when ahead.
Your job is to make the variance real, not to add a multiplier.

## The central problem

A goalie is worth a lot on a night they start and nothing on a night they do not.
So the entire valuation reduces to: **will this goalie start, and how sure are we?**

The existing code already made the right call here and it should survive.
From `docs/autonomous-agent.md`:

> This replaced an earlier probabilistic approach that gave a 67% starter only 67% of their normal FPTS on every game. The new logic gives full output on projected starts and zero on projected rests.

Averaging a goalie's value across games they will not play produces a phantom player who is mildly useful every night and actually useful never.
Predict discrete starts.

Then, separately, carry `confidence` on the `Candidate` so the planner knows how firm the prediction is.
Expectation and certainty are different numbers and the contract has a field for each.

## Fix first: the leakage bug

`src/optimize/goalies.py` has a documented time-leakage bug.
`compute_goalie_game_log`, `compute_crease_share`, and `compute_opponent_softness` filter by `game_id` range:

```python
game_id_min = season_prefix * 1_000_000 + 20_000
game_id_max = season_prefix * 1_000_000 + 30_000
```

That bounds the *season*, not the date.
Every one of these functions sees the full season regardless of `as_of`, so any backtest using goalies is reading the future.

Fix it before anything else.
Every function in this module takes `as_of` and filters on `Game.date < as_of`, strictly.
Join through `Game` rather than inferring dates from `game_id`.

This is listed in `CLAUDE.md` as a known unfixed bug.
Remove that note when it is fixed.

## Part 1: Start prediction

For each goalie and each day in the window, produce `p_start` in `[0, 1]`.

Sources in priority order:

1. **Confirmed.** `GoalieStart` table has a row. `p_start = 1.0` (or `0.0` for the other goalie on that team). `confidence = 1.0`.
2. **Crease share.** Fraction of the team's starts this goalie has taken over a trailing window, computed with `as_of` respected. A 70% starter gets roughly `p_start = 0.7` on a normal night.
3. **Back-to-back adjustment.** Teams almost never start the same goalie on both nights of a back-to-back. On the second night of a back-to-back, invert: the backup's `p_start` rises sharply and the starter's collapses. Detect back-to-backs from the schedule, not from a heuristic about weekdays.
4. **No data.** Rookie or newly acquired goalie with no crease history. Return no candidate rather than a guessed one. See the contract rule about not defaulting unknowns to zero.

The spec says a goalie pipeline will eventually provide probable starters with confidence directly.
Define the interface so that pipeline can drop in:

```python
class StarterSource(Protocol):
    def probable_start(self, nhl_id: int, game_date: date, as_of: date) -> tuple[float, float] | None:
        """Return (p_start, confidence), or None if unknown."""
```

Ship a `DerivedStarterSource` built from `GoalieStart` plus crease share.
Leave room for an external one.

## Part 2: Per-start value

Expected fantasy points for a start, using `GOALIE_WEIGHTS` from `src/core/scoring.py`.

Inputs:

- **Goalie quality.** Save percentage and goals-against derived from `shot_attempts`, which carries `goalie_id` on every shot. The existing derivation in `compute_goalie_game_log` is sound once the date filter is fixed.
- **Opponent softness.** How many goals the opponent scores and allows. Existing `compute_opponent_softness` blends 60/40 with goalie quality; keep the structure, re-derive the weights if the data supports something better.
- **Win probability.** Wins are worth a lot in most scoring systems. A good goalie on a bad team is worth less than their save numbers suggest. Use team strength, not just goalie strength.

Then:

```
projections[day] = p_start * expected_fpts_for_a_start
```

The `Candidate` contract says `projections` holds expectation, already multiplied out.
A day with `p_start` near zero should be absent from the mapping, not present as `0.0`.

## Part 3: Goalie variance

This is the part that makes the spec's instinct work, and it is a dependency you must coordinate with P1.

Goalie game outcomes are much wider than skater outcomes.
A shutout win and a pulled-after-two-periods loss are both routine, and in most scoring systems they are separated by fifteen or more fantasy points.
The skater variance model that P1 fits will badly understate this.

Two sources of variance compound:

1. **Outcome variance given a start.** Wide, and measurable the same way P1 measures skater residuals.
2. **Start uncertainty.** A 60% starter contributes a Bernoulli term. Its variance is `p * (1 - p) * value^2`, which for `p = 0.5` and a 7-point start is larger than the outcome variance itself.

Both belong in the model:

```
var = p_start * outcome_var + p_start * (1 - p_start) * expected_start_value**2
```

The split with P1 is already pinned in `01-contract.md` section 8, so there is nothing to negotiate.
P1 owns `game_sigma(projected_fpts, player_type)` and the skater branch.
You own `goalie_game_var(p_start, start_value, outcome_var)` and the fitted goalie coefficients, which P1's `game_sigma` dispatches into.

P1 will have stubbed the goalie branch to fall back to the skater curve.
Replace the stub; do not change the signature or restructure P1's module.

## Part 4: The confirmation window

Goalie confirmations arrive on game day, typically late morning through the afternoon.
The spec identifies the strategic consequence:

> If a given backup goalie ended up getting two starts, but neither of them were confirmed at the beginning of the week, we wouldn't have that data to go off of. And if we made all our transactions on the Monday, we wouldn't be able to capitalize on that opportunity.

That is an argument for holding an add, and it is P6's option-value calculation, not yours.
What you owe P6 is an honest `confidence`, so a Monday plan can see that Thursday's goalie picture is genuinely unresolved rather than merely unfavorable.

Do not encode "wait for confirmations."
Report uncertainty accurately and let the planner price it.

## Acceptance scenarios

`tests/optimize/week/test_goalies.py`.

**Leakage, first and non-negotiable**

- `crease_share_respects_as_of`: a goalie who starts every game in the back half of the season shows a low crease share when `as_of` is set before that stretch.
- `game_log_respects_as_of`.
- `opponent_softness_respects_as_of`.

Write these three before writing any new logic.
They fail against the current code, which is the point.

**Start prediction**

- `confirmed_start_overrides_crease_share`: a `GoalieStart` row wins over a 30% crease share.
- `backup_gets_the_second_night_of_a_back_to_back`.
- `unknown_goalie_produces_no_candidate` rather than a zero-projection one.
- `starter_and_backup_p_start_sum_to_about_one` on a normal night.

**Valuation and variance**

- `goalie_sigma_exceeds_skater_sigma_at_equal_mu`. If this fails, the variance model is wrong and the whole point of the package is lost.
- `coin_flip_starter_has_higher_variance_than_a_confirmed_starter_with_the_same_mean`. This is the Bernoulli term doing its job.
- `goalie_competes_in_the_same_pool`: a goalie projected at 7 FPTS for one start and a skater at 2.5 across three games both appear as `Candidate` objects and are directly comparable to the planner.

**Behavior under P(win)**

- `trailing_late_prefers_the_volatile_goalie`: two goalies with equal mean, different variance, matchup trailing on the final day. The higher-variance one produces the larger `DELTA P(win)`.
- `leading_late_prefers_the_steady_goalie`: same setup, leading. The lower-variance one wins.

Those two are the payoff test for the whole objective change.
If they pass, the spec's goalie instinct is satisfied without a single weight.

## Do not

- Do not add a goalie multiplier, bonus, or priority anywhere. If goalies deserve to win a slot, `P(win)` will say so.
- Do not build a goalie replacement level. The spec is explicit that goalie value is volume-driven, not rate-driven, and `ReplacementLevel` deliberately excludes goalies. P4 keeps it that way.
- Do not modify `src/optimize/week/supply.py`. P2 owns it. You produce a separate `CandidateSource` that P6 merges.
- Do not change `GOALIE_WEIGHTS` or anything in `src/core/scoring.py`.

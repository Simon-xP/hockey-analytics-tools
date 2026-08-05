# P2: Skater Supply (prescan and add candidates)

**Owns:** `src/optimize/week/supply.py`

**Depends on:** P0, P1

**Blocks:** P6

Read `00-overview.md` and `01-contract.md` first.

## Purpose

Produce the pool of skaters worth considering as adds, cheaply enough that the planner can run every few minutes.

The spec calls out the algorithm directly, and it is the right one:

> If our plan first does some type of scan on the team level, and then compares possible plans against each other based on the quality of players that are available on those teams, that could be a huge avenue for a good algorithm.

That is the structure. Schedule first, players second.

## Why prescan at all

Naive approach: value every free agent over every window day.
The current code does a degraded version of this and it is both slow and wrong.

The insight is that **most of a streaming add's value comes from its schedule, not its identity**.
There are roughly 32 team schedules but hundreds of free agents.
Score the 32 first, then only look at players on the schedules that scored well.

## Part 1: Day patterns

For a window of `D` days, each NHL team has a bitmask of which days it plays.

```
window = [Mon Tue Wed Thu Fri Sat Sun]
EDM    =  1   0   1   0   0   1   0     -> plays Mon, Wed, Sat
```

Teams play three or four games a week, so 32 teams collapse to roughly 15 to 25 distinct patterns.
Group teams by pattern.

## Part 2: Pattern value

For each distinct pattern, compute an **upper bound** on what a player with that schedule could be worth.

```python
def pattern_value(grid: LineupGrid, pattern: frozenset[date], reference_fpts: float) -> float
```

Insert a hypothetical player projected at `reference_fpts` per game on each day in the pattern and sum the grid's marginal.

Two properties make this work:

- On a day where a slot is open, marginal equals the full projection.
- On a day where every slot is full, marginal is the projection minus the weakest displaced starter, which is often near zero.

So the pattern score naturally encodes the "off nights" intuition from the spec without anyone encoding off nights.
A pattern that lands on days your lineup is already full scores badly, and it should.

Set `reference_fpts` to a fixed percentile of the pool's projected rates (the 75th is a reasonable start) so patterns are comparable to each other.
It is a ranking device, not a valuation.

**Position matters.** A pattern's value depends on which slot the player would fill, because your open slots differ by position and day. Compute pattern value **per position group** (F, D) at minimum. Do not collapse them.

## Part 3: Candidate selection

Take the union of three sources, then deep-value only the union.
The union, not a single ranked list, because each source catches something the others miss.

1. **Top patterns.** Every available skater on a team in the top `K` patterns for their position group. Start with `K = 6` and tune against the recall test below.
2. **Top rates regardless of schedule.** The best `N` available skaters by projected FPTS per game, whatever their schedule. This is the safety net that stops the prescan from pruning a genuinely good player who happens to play Tuesday and Saturday.
3. **Signal-flagged players.** Anyone with a high `upside_score` or `opportunity_score`, or a large recent ownership delta. These are the breakout candidates the spec worries about losing to another manager, and they can be invisible to both of the above.

Deep valuation means: per-day projections from the state's projection cache, terminal value, and the signal scores.

### Fix: the free agent pool is currently arbitrary

`week/light.py::get_free_agent_nhl_ids` returns every player with a `yahoo_player_id` who is not on a `TeamRoster`, **in no particular order**.
`heavy.py` then slices `[:fa_candidate_limit]`, taking an arbitrary 60.
There is no ordering, so which 60 you get depends on database row order.

Fix it here.
Order the pool before any truncation, and make the truncation happen after scoring, not before.

Also: pulling free agents from Yahoo with a single sort (actual rank) systematically misses players who are good on dimensions that sort does not surface.
Pull from several sorts and union the results.
This is a known wanted improvement and this package is the natural home for it.

## Part 4: Availability

```python
available_from: date | None
```

A dropped player sits on waivers for `league.waiver_days` before becoming a free agent.
Candidates on waivers are not addable today, but they **are** plannable: "Player X clears Thursday, plan the add then" is a real and valuable move, and the spec describes it directly.

Set `available_from` and let P6's search filter per day.
Do not exclude waiver players from the pool.

## Acceptance scenarios

`tests/optimize/week/test_supply.py`.

**Prescan behavior**

- `pattern_on_full_days_scores_near_zero`: given a grid where every slot is full Tuesday and Saturday, a Tue/Sat-only pattern scores far below a Mon/Wed pattern of the same game count.
- `four_games_beats_three_when_slots_are_open`: with an empty lineup, more games wins.
- `three_games_beats_four_when_slots_are_blocked`: with a lineup full on three of the four-game team's days, the three-game team on open days wins. This is the off-nights intuition, emerging rather than encoded.
- `pattern_value_differs_by_position`: same pattern, different value for F and D when open slots differ.

**Recall, the test that matters**

- `prescan_recall`: on at least ten historical dates, run a full valuation over the entire FA pool, take the top 20 by window value, then run the prescan pipeline and measure what fraction of those 20 survive. **Target 0.95 or better.** Below that, raise `K` or widen source 2 until it clears. Report the actual number in the test output; do not just assert a boolean.

Recall is the whole risk of this package.
Pruning is only free if it prunes nothing that mattered.

**Availability**

- `waiver_player_is_in_the_pool_with_a_future_date`.
- `waiver_player_not_addable_before_clearing`: P6 will enforce this, but the candidate must carry the date correctly.

**Leakage**

- `candidate_projections_respect_as_of`: two states with different `as_of`, same window. The earlier one must not benefit from games it should not see.

## Do not

- Do not handle goalies. P3 owns them and produces `Candidate` objects that merge into the same pool.
- Do not score or rank moves. You produce candidates; P6 decides.
- Do not filter by hold value or droppability. That is P4's job and it applies to the roster side, not the add side.
- Do not call `forecast_player()` directly. Read `WeekState`'s projection cache. If a projection you need is missing from the cache, that is a P1 bug; report it rather than working around it.

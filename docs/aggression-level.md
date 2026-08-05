# Matchup State Engine

## Context

The aggression level (CONSERVATIVE / NORMAL / AGGRESSIVE / DESPERATE / PREPARE) drives every transaction decision in PuckAgent: quality vs schedule weighting, fire thresholds, injury penalties, and evaluation alpha.
Today it is always hardcoded to NORMAL.
The existing desperation.py has a simple margin-based decision tree that ignores time remaining, league context, and never actually runs in production.

This plan replaces it with a proper matchup state engine that computes aggression dynamically from live scoreboard data, our forecast models, and win probability math.

## Mathematical Model

### Base win probability

Normal-distribution win probability (CLT-appropriate for sums of per-game FPTS):

```
my_remaining   ~ N(mu_me,  sigma_me^2)
opp_remaining  ~ N(mu_opp, sigma_opp^2)
```

Variance per team comes from summing per-game variances across all remaining fillable games.
Per-game variance = (CV * fpts_per_game)^2, where CV (coefficient of variation) is ~0.45 for hockey FPTS.
CV just means "standard deviation as a fraction of the mean" -- a 5 FPTS/game player swings by ~2.25 points game to game.

When remaining_games = 0, variance = 0 and P(win) is deterministic.
This is why a 50-point gap on Monday (many games left, high variance) yields a different aggression than the same gap on Saturday (few games, low variance).

### Opponent pickup boost

The opponent can improve their score by making pickups with their remaining adds.
We model this as a distribution and fold it into their remaining-points projection:

```
opp_pickup_boost ~ N(mu_boost, sigma_boost^2)
```

To compute this:
1. Determine opponent's remaining adds for the week (league add limit minus their adds used).
2. Simulate their optimal pickups: run our forecast models on the best available free agents for their open roster slots on remaining game days.
3. mu_boost = sum of projected FPTS from optimal pickups.
4. sigma_boost = sqrt(sum of per-pickup variance), using the same CV-based per-game variance.

The opponent's full remaining distribution becomes:

```
opp_total ~ N(mu_opp + mu_boost, sigma_opp^2 + sigma_boost^2)
```

Our own remaining pickup potential is modeled the same way and folded into the gap calculation.
Both sides getting credit for their best pickups keeps P(win) balanced.

### Final P(win)

```
gap = (my_earned + mu_me + my_boost) - (opp_earned + mu_opp + opp_boost)
combined_sigma = sqrt(sigma_me^2 + sigma_opp^2 + my_boost_sigma^2 + opp_boost_sigma^2)

P(win) = Phi(gap / combined_sigma)
```

## Aggression Mapping

```
+----------------+-----------------------------+
| P(win) range   |       Base level            |
+----------------+-----------------------------+
| > 0.85         | CONSERVATIVE (cruising)     |
+----------------+-----------------------------+
| 0.55 - 0.85    | NORMAL                      |
+----------------+-----------------------------+
| 0.25 - 0.55    | AGGRESSIVE                  |
+----------------+-----------------------------+
| < 0.25         | DESPERATE                   |
+----------------+-----------------------------+
```

### PREPARE state

PREPARE is a special state that shifts the optimization horizon from the current week to next week.
When in PREPARE, the agent discards how valuable a player is to our team this week and only evaluates players by their value next week.
This means pickups target next week's schedule, and drops ignore current-week remaining games.

PREPARE availability depends on the week's importance tier (see below).

### Week importance

`WeekImportance` classifies how much this week's matchup matters for the season.
It determines which aggression levels are available and whether PREPARE can trigger.

```
+---------+-------------------------------------------------------------------+
| Tier    | Behavior                                                          |
+---------+-------------------------------------------------------------------+
| NEUTRAL | Cap at AGGRESSIVE (never DESPERATE). PREPARE triggers on both     |
|         | extremes: winning (> 0.95) and lost cause (< 0.05).               |
+---------+-------------------------------------------------------------------+
| BIG     | Full range including DESPERATE. PREPARE triggers on both          |
|         | extremes: winning (> 0.95) and lost cause (< 0.05).              |
+---------+-------------------------------------------------------------------+
| CRAZY   | Full range including DESPERATE. PREPARE never triggers.           |
|         | Only this week matters, never optimize for next week.             |
+---------+-------------------------------------------------------------------+
```

Auto-detection from standings context:
- **NEUTRAL**: Comfortably in or out of playoff contention.
- **BIG**: On the playoff bubble (within 2 spots of cutoff).
- **CRAZY**: Fantasy playoffs, or final 2 weeks while on the bubble.

Can also be manually overridden per-week.

## Team Roster Tracking

### Problem

To project any team's remaining points (not just ours), we need to know their roster.
Currently, roster state is reconstructed on-the-fly from `YahooDraftPick` + `YahooTransaction` records, but only for our own team.

### Solution: TeamRoster table

New model `TeamRoster` in `src/core/models/yahoo_fantasy.py`:

```
TeamRoster:
  league_key: str
  team_key: str
  nhl_id: int
```

Current-state snapshot of every team's roster.
Simple: add inserts a row, drop deletes the row.

Maintained incrementally:
- **Initial load**: When we first sync a league, populate from Yahoo's current rosters for all teams (via league roster endpoint).
- **Incremental updates**: When we sync transactions (`YahooTransaction`), insert on add, delete on drop.
- **Periodic full sync**: Optionally re-fetch all team rosters periodically as a consistency check.

For historical roster queries (backtesting), we continue using the existing reconstruction from `YahooDraftPick` + `YahooTransaction` records.

This enables:
- Projecting any opponent's remaining points using `forecast_player()` on their roster.
- Computing optimal pickups for any team (to model their pickup boost distribution).
- Fast "who's on team X right now?" queries without replaying transaction history.

## Module Structure

New module: `src/optimize/matchup/`

```
src/optimize/matchup/
    __init__.py            # Public API: determine_aggression()
    models.py              # MatchupSnapshot, TeamProjection, MatchupContext, WinProbability
    scoreboard.py          # Fetches + parses Yahoo scoreboard; DB reconstruction for backtest
    projections.py         # Projects remaining points for any team using forecast models
    pickup_model.py        # Models opponent's optimal pickup boost as a distribution
    win_probability.py     # Normal-distribution P(win) calculation with pickup boost
    state_engine.py        # Maps P(win) + context -> AggressionLevel (including PREPARE)
```

### models.py

- **MatchupSnapshot**: Raw scoreboard data (earned scores, projected totals, team keys, week dates, adds_remaining for each team)
- **TeamProjection**: Remaining-points projection for one team (earned, mu_remaining, sigma_remaining, remaining_games, remaining_fillable_games)
- **PickupBoost**: Distribution of points from optimal pickups (mu_boost, sigma_boost, n_adds_remaining, top_targets)
- **MatchupContext**: Full input to the state engine (snapshot, both team projections, both pickup boosts, standings context, must_win flag)
- **WinProbability**: Result (p_win, projected_gap, combined_sigma, reasoning strings)

### scoreboard.py

New Yahoo client function `get_scoreboard()` in `src/ingest/yahoo/client.py`:
- Endpoint: `/league/{league_key}/scoreboard;week={week}`
- Parses earned and projected points from each matchup
- Returns structured dict with both teams' scores and projections

Module function `fetch_matchup_snapshot()` wraps the client call into a MatchupSnapshot, including each team's adds_remaining for the week.

For backtesting: `build_matchup_snapshot_from_db()` reconstructs scores from actual game stats up to as_of.

### projections.py

Projects remaining points for any team (not just ours):
1. Look up team's roster from `TeamRoster` table.
2. Get remaining games for each player from the schedule.
3. Call `forecast_player()` for each player-game pair.
4. Use `assign_players_to_slots()` to count only fillable games (respects roster slot limits).
5. Return `TeamProjection` with mu and sigma.

Reuses: `forecast_player()` from `src/predict/forecasting/forecast.py`, `assign_players_to_slots()` from `src/optimize/slots.py`.

### pickup_model.py

Models the expected point boost from a team's remaining adds:
1. Get the team's adds_remaining for the week.
2. Get the free agent pool (players not on any team's `TeamRoster`).
3. For each remaining game day, find the best available FA for open roster slots.
4. Run `forecast_player()` on the top candidates.
5. Greedily assign optimal pickups across remaining adds.
6. Return `PickupBoost` distribution (mu_boost, sigma_boost).

This runs for the opponent to make P(win) realistic.
Can optionally run for our team too (useful for deciding if we should save adds).

### win_probability.py

Pure function `compute_win_probability(ctx: MatchupContext) -> WinProbability`.
Combines team projections + pickup boosts into final P(win) using `scipy.stats.norm.cdf`.

### state_engine.py

Pure function `determine_aggression(ctx: MatchupContext) -> tuple[AggressionLevel, WinProbability]`.

1. Compute P(win)
2. If not must_win, check PREPARE triggers:
   - P(win) > 0.95 -> PREPARE
   - P(win) < 0.05 -> PREPARE
3. If must_win or not in PREPARE range, map to base aggression via thresholds
4. Return level + full WinProbability for transparency

### __init__.py - Public API

```python
def determine_aggression(
    session, league_key, yahoo_week=None, roster=None,
    my_rank=8, is_playoff=False, must_win=None, sim_date=None,
) -> tuple[AggressionLevel, MatchupContext]:
```

Also exposes `determine_aggression_from_context()` for backtesting/testing (pure function, no Yahoo/DB calls).

## Integration Changes

### Yahoo client (src/ingest/yahoo/client.py)

- Add `get_scoreboard(league_key, week)` function.
- Add `get_all_rosters(league_key)` to fetch every team's current roster for initial `TeamRoster` population.
- Enhance `get_matchup()` to return adds_remaining per team.

### New model (src/core/models/yahoo_fantasy.py)

- Add `TeamRoster` model with league_key, team_key, nhl_id.
- Alembic migration.

### TeamRoster sync (src/ingest/yahoo/sync.py)

- When syncing transactions, also update `TeamRoster` rows.
- Add `sync_all_rosters(session, league_key)` for initial/periodic full sync.

### AggressionLevel enum (src/optimize/models/)

- Add `PREPARE` to the enum.
- Add weights tuple for PREPARE (quality-focused but for next week's schedule context).

### Deprecation bridge (src/optimize/desperation.py)

- Rewrite `compute_aggression()` and `compute_aggression_from_yahoo()` to construct a MatchupContext and delegate to the new module.
- Keep the function signatures for backward compatibility.

### Backtest engine (src/backtest/engine.py)

- Add `auto_aggression: bool = False` to BacktestConfig.
- When enabled, call `determine_aggression_from_context()` each decision day using reconstructed matchup data.

## Implementation Order

1. `src/core/models/yahoo_fantasy.py` (add TeamRoster model) + Alembic migration
2. `src/optimize/models/matchup.py` (data classes, no dependencies)
3. `src/optimize/matchup/win_probability.py` (pure math, testable immediately)
4. `src/optimize/matchup/state_engine.py` (pure logic on models, includes PREPARE)
5. `tests/optimize/matchup/test_win_probability.py` + `test_state_engine.py`
6. `src/ingest/yahoo/client.py` (add get_scoreboard, get_all_rosters)
7. `src/ingest/yahoo/sync.py` (TeamRoster sync logic)
8. `src/optimize/matchup/scoreboard.py` (Yahoo parsing)
9. `src/optimize/week/light.py` (wire up forecast pipeline for any team)
10. `src/optimize/week/light.py` (opponent pickup boost distribution)
11. `src/optimize/matchup/__init__.py` (compose everything)
12. `src/optimize/models/` (add PREPARE to AggressionLevel)
13. `src/optimize/desperation.py` (deprecation bridge)
14. Backtest integration
15. Scenario calibration (see Verification)

## Verification

### Unit tests

Pure-function tests for `win_probability` and `state_engine`:
- Monday 50-pt gap behind -> AGGRESSIVE (high variance, still winnable)
- Saturday 50-pt gap behind, 1 game left, not must_win -> PREPARE (lost cause)
- Saturday 50-pt gap behind, 1 game left, must_win -> DESPERATE (must_win blocks PREPARE)
- Large lead any day -> CONSERVATIVE or PREPARE depending on P(win) threshold
- P(win) > 0.95 -> PREPARE (already won, optimize next week)
- P(win) > 0.95 and must_win -> CONSERVATIVE (don't risk it in a must-win)
- Tied on bubble -> AGGRESSIVE
- Playoffs -> must_win auto-set, never PREPARE
- P(win) boundary conditions (0, 0.5, 1.0)
- Variance decay: same gap yields different P(win) on Mon vs Sat
- Opponent pickup boost shifts P(win) meaningfully when they have many adds left

### Pickup model tests

- Opponent with 0 adds remaining -> PickupBoost(0, 0)
- Opponent with 3 adds and good FAs available -> non-trivial mu_boost
- Empty FA pool -> PickupBoost(0, 0)

### Scenario calibration

Walk through specific matchup scenarios using our default scoring (G=3, A=2, PIM=0.3, SOG=0.3, HIT=0.4, BLK=0.5).
For each scenario:
1. Define: day of week, my earned score, opponent earned score, remaining games for each team, opponent adds remaining, must_win status.
2. User states what aggression level they would intuitively choose.
3. Model computes P(win) and aggression level.
4. Compare and adjust thresholds if needed.

This is the primary calibration mechanism.
The P(win) thresholds (0.05, 0.25, 0.55, 0.85, 0.95) are starting points to be tuned through this process.

### Integration

- Call `determine_aggression()` with a real DB session and verify sensible results.
- Verify `TeamRoster` sync correctly tracks roster changes.

### Backward compat

- Verify existing `compute_aggression()` still works via the deprecation bridge.

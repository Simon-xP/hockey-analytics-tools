# PuckAgent — Autonomous Fantasy Hockey Agent

## Mission

Build an AI agent that autonomously manages a fantasy hockey team
to a championship-level performance. Single user (the owner), maximum
speed, maximum edge. The goal is to "solve" fantasy hockey.

## What "Solving" Means

Fantasy hockey skill breaks down roughly as:

1. **Transactions (60%)** — picking up the right player before others
2. **Lineup optimization (25%)** — starting the right players each day
3. **Trades (10%)** — buying low, selling high
4. **Draft (5%)** — one-time event, less leverage over a full season

The agent's biggest edge is speed and consistency on #1 and #2.
A human checks their team once or twice a day. The agent checks
every 2-3 minutes and acts instantly.

## Architecture

```
┌─────────────────────────────────────────────┐
│                AGENT LOOP                    │
│  Polls every 2-3 min during active hours     │
│                                              │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ Monitor  │→ │ Evaluate │→ │  Execute  │  │
│  │ (detect) │  │ (decide) │  │  (act)    │  │
│  └──────────┘  └──────────┘  └───────────┘  │
└─────────────────────────────────────────────┘
         │              │              │
    ┌────┴────┐    ┌────┴────┐   ┌────┴────┐
    │ Yahoo   │    │ Forecast│   │ Yahoo   │
    │ Daily   │    │ Model   │   │ Write   │
    │ Faceoff │    │ Scoring │   │ API     │
    │ NHL API │    │ Logic   │   │         │
    └─────────┘    └─────────┘   └─────────┘
```

### Monitor (every 2-3 min)

Detects actionable events:
- **Injury alert**: starter goes down → their backup becomes valuable
- **Goalie confirmation**: backup gets the start → stream opportunity
- **Line promotion**: player moves to top line or PP1
- **Roster move**: another team drops a good player
- **Cold streak**: rostered player underperforming → consider drop
- **Hot streak**: free agent breaking out → consider add

Data sources polled:
- Yahoo free agents (ownership changes, new drops)
- Daily Faceoff (goalie starts, line combos, injury updates)
- NHL API (game scores, schedule changes)

### Evaluate (on each trigger)

For each detected event, compute:

1. **Pickup value**: projected FPTS/GP of the free agent × remaining
   games this week × matchup quality
2. **Drop cost**: projected FPTS/GP of the worst rostered player ×
   their remaining games
3. **Net gain**: pickup value - drop cost
4. **Confidence**: how certain are we? (confirmed starter > rumored,
   20-game sample > 3-game sample)

Decision rules:
- **Auto-execute** if net gain > threshold AND confidence > threshold
- **Queue for review** if net gain > 0 but confidence is marginal
- **Ignore** if net gain ≤ 0

### Execute

- Add/drop via Yahoo API (requires write scope)
- Log every transaction with reasoning
- Set daily lineup based on projections + matchup quality
- Send notification to owner (what was done and why)

## The Transaction Evaluator

Built out in `src/optimize/`. The core decision engine for
add/drop evaluation. Structurally complete as of April 2026, still
being tuned against live data.

### Architecture

```
recommend(league_key, week) → WeekPlan
  │
  ├─ load_roster_from_yahoo()              ← live Yahoo API, no static file
  ├─ compute_replacement_level()            ← top-5 avg of FA pool per position
  ├─ rank_drops(roster)                     ← most droppable first
  │    └─ compute_position_scarcity()       ← roster-relative, not league
  ├─ compute_player_value(fa) for each FA   ← slot-aware weekly projection
  │    └─ forecast_fn = v2 pipeline         ← situation-split, per-game
  ├─ goalie_stream_to_player_value()        ← deterministic start prediction
  └─ optimize_week()                        ← greedy with look-ahead, 4 adds
```

### Scoring formula

```
score = weekly_gain * week_weight
      + ros_gain_normalized * ros_weight
      - position_scarcity_penalty
      + upside_bonus
```

`week_weight` and `ros_weight` shift based on `AggressionLevel`:
- DESPERATE: 0.9 / 0.1 (win this week at all costs)
- AGGRESSIVE: 0.7 / 0.3
- NORMAL: 0.5 / 0.5
- CONSERVATIVE: 0.3 / 0.7 (protect long-term assets)

Aggression is computed from matchup context in `desperation.py`
(matchup margin, standings position, playoff flag).

### Key design decisions

**Forecast as black box.** The evaluator calls
`forecast_fn(nhl_id, game_date, avg_toi) -> float` and treats it as
opaque. Currently wraps the v2 model's full pipeline (extract features,
predict per-60 rates per situation, predict TOI per situation, combine
via `project_per_game`). The evaluator doesn't know or care about the
model internals.

**Slot-aware projections for add candidates.** Weekly FPTS is NOT
`fpts_per_game * games`. It only counts games where the player can make
the active lineup, determined by `analyze_week()` + `assign_players_to_slots()`.
A player whose team plays 4 games but is bench-blocked on 2 of them is
worth 2 games, not 4.

**Simple valuation for drop candidates.** For rostered players, we use
historical FPTS/GP × all games (no slot check). The question is "what do
we lose by dropping this player?" — the answer includes all their games
since dropping them frees a slot for replacements. If we used slot-aware
logic here, a bench-blocked roster player would look like 0 value and
we'd always drop them, which is wrong.

**Goalies use deterministic start prediction, not probabilistic.**
Goalies either start or they don't. For each game this week:
- If in `GoalieStart` table → use that
- Else if goalie is classified as "starter" (≥60% crease share) →
  assume they start unless it's a back-to-back second night
- Else (committee/backup without confirmation) → assume they don't start

This replaced an earlier probabilistic approach that gave a 67% starter
only 67% of their normal FPTS on every game. The new logic gives full
output on projected starts and zero on projected rests.

**Goalie stats derived from shot_attempts.** No separate goalie game
stats table needed. Every shot has `goalie_id`, and we compute saves,
GA, wins (from Game scores), and shutouts per game. Goalie quality is
blended 60/40 with opponent softness.

**Rookies deferred.** Players with no significant historical data
(e.g. Porter Martone) return 0 projections and are effectively ignored.
We don't draft rookies anyway — they'll be incorporated later via
prospect handling (projected TOI ramp-up, comparable-player matching, etc.)

**Unified goalie/skater candidate pool.** Both produce `PlayerValue`
with the same fields. A goalie projected at 7 FPTS for 1 start
competes directly against a skater at 2.5 FPTS × 3 games in the same
pool. No separate decision paths. (Wiring this into `recommend()` is
still a loose end — the weekly_optimizer accepts both but `recommend()`
currently only passes skater FAs.)

### Known loose ends

1. **Upside model reads GameIndividualStats** (NST, no 2025-26 data).
   Needs port to `GameAdvancedStats` which has current-season data.
2. **Desperation metric untested** — needs opponent roster projection
   via Yahoo API.
3. **Backtest harness never run** — needs historical roster snapshots.
4. **Goalies not in `recommend()` pool yet** — framework exists, wiring
   is pending.
5. **Position scarcity edge case** — Adam Fox on a 4-D-eligible roster
   still gets nonzero scarcity; the formula over-weights "tightness."

## Lineup Optimizer

Daily lineup setting, run each morning:

1. Get today's games and goalie starts
2. For each roster player with a game today:
   - Compute projected FPTS for today (opponent, home/away, B2B, etc.)
3. Assign players to slots to maximize total projected FPTS
4. Use bipartite matching (already built in schedule optimizer)
5. Set lineup via Yahoo API

Edge cases:
- Goalie start confirmed late → re-optimize
- Player is game-time decision → have backup plan ready
- B2B situations → sometimes bench the star to start the streamer

## Polling Schedule

Active hours: 6 AM - midnight ET (covers all NHL game times)

| Time | Frequency | What |
|------|-----------|------|
| 6 AM | Once | Set initial lineups for the day |
| 6 AM - 4 PM | Every 5 min | Monitor injuries, goalie starts, line changes |
| 4 PM - 7 PM | Every 2 min | Pre-game window — goalie confirms come in, last-minute roster changes |
| 7 PM - midnight | Every 3 min | Games in progress — monitor for mid-game injuries, check scores |
| Post-games | Once | Update stats, evaluate next-day actions |

## Yahoo API Budget (Single User)

~20 calls per refresh cycle:
- My roster: 1 call
- Free agents: 1 call (25 players)
- League transactions: 1 call
- Standings: 1 call (occasionally)

At every 3 min for 18 hours: ~360 cycles × 4 calls = ~1,440 calls/day.
Yahoo limit: ~2,000/hour. We'd use ~80/hour. Plenty of headroom.

## Confidence & Safety

The agent should not make dumb moves. Safety mechanisms:

- **Minimum confidence threshold**: don't act on 1-game samples
- **Cool-down period**: max 3 transactions per day (configurable)
- **Protected players**: user can mark players as un-droppable
- **Transaction log**: full audit trail of every decision + reasoning
- **Notification system**: alert the owner on every action
- **Kill switch**: owner can pause the agent instantly

## Agent Monitor UI

The frontend is an agent monitoring console first and a manual toolbox second.
The agent runs in several leagues at once, most of them tests, so the surface is
built around switching between them and reading each agent's current state.

### Layout

| Route | Page | What it is |
|-------|------|------------|
| `/` | `pages/Fleet.jsx` | Every league at a glance: state, matchup score, win probability, next planned move, moves used, per-league and fleet-wide pause/kill |
| `/league/:leagueId` | `pages/Agent.jsx` | One league in depth |
| `/dashboard`, `/players`, `/roster`, `/adds`, `/trades`, `/streamable-goalies`, `/injuries` | unchanged | Legacy manual tools, collapsed under "Legacy tools" in the sidebar |

League switching happens in the sidebar, which lists every league with a status
dot. There is no in-page league dropdown.

### The league page

Always visible at the top:

1. **Control bar**: state pill, last/next run, aggression, move cap, execution
   mode, pause, kill. A banner explains why an agent is paused or stopped.
2. **Matchup banner**: my score vs opponent, projected finals, win probability,
   games remaining, per-day bars for scoring so far and projected.

Only **execution mode** (auto-execute vs review-first) is editable. Aggression
and the move cap are shown but read-only: they come out of the model and the
safety config, and are changed in code.

Then five tabs:

- **Week plan**, the centre of gravity now that decisions are weekly rather than
  daily. Header stats: projected total with the plan, the do-nothing baseline,
  plan value, win probability with and without the plan, moves pending. Then a
  day-by-day week strip, then the planned transactions in one of two views:
  - **Grid** (default): one block per move, rows for the add and the drop,
    columns for each remaining day. Cells are colour-coded as gained, kept,
    lost, or not yet ours, with the fire day marked and a per-day net row. This
    makes schedule-aware deferral legible: firing Friday shows Thursday's drop
    game as *kept* rather than lost.
  - **List**: the same moves as prose cards with the full rationale.
  Both views carry per-move controls (approve, hold, fire now, cancel) and a
  collapsed list of moves the agent considered and rejected.
- **My team**: roster with games left, FPTS/GP, projected weekly FPTS, and the
  agent's annotations (protected, drop candidate, recent add). Protect is
  toggleable inline.
- **Activity**: the transaction log. What the agent did, why, the candidates it
  weighed, and the realized outcome where known.
- **Manager moves**: other managers' transactions, graded.
- **Watchlist**: rising free agents, roster players at risk, confirmed goalie
  starts, active injuries.

There is deliberately no self-assessment panel. Agent quality is judged from the
backtest harness and from reading the decisions, not from the agent's own
scorecard.

**STATUS: mock data only.** Every value comes from `frontend/src/mock/agentData.js`,
routed through the in-memory store in `frontend/src/state/agentStore.jsx`. The
controls mutate that store and nothing else: no Yahoo call, no persistence across
a reload. Mock numbers are internally consistent on purpose (each move's gain
equals the sum of its daily net, and baseline plus counted move gains equals the
projection in the matchup banner), so a number that looks wrong on screen is a
bug, not mock noise.

The mock shape mirrors the eventual backend:

| Mock field | Real source when Phase 2 lands |
|------------|-------------------------------|
| `league.status` / `mode` / `aggression` | agent daemon state + `AggressionLevel` |
| `league.matchup` | `src/optimize/matchup` (both-team projections) |
| `league.plan` | `src/optimize/week` -> `WeekPlan` |
| `league.transactions` | transaction log table (not built) |
| `league.roster` | Yahoo roster + `src/predict` projections |

Wiring is then a swap of the store's data source for fetches against
`/api/agent/*` (routes not yet defined). Do not treat anything on these pages as
truthful until that happens. Both pages carry a MOCK DATA tag for that reason.

## What We Already Have

- [x] v2 situation-split forecast model (5v5/PP/PK/Other, XGBoost per stat)
- [x] Fantasy scoring weights (league-specific)
- [x] Opponent quality metric (streaming score / softness)
- [x] Schedule optimizer with bipartite matching
- [x] Yahoo OAuth + read API (roster, free agents, standings)
- [x] Daily Faceoff scraper (goalies, lines, injuries)
- [x] NHL API client (scores, rosters, game data)
- [x] Player resolver (Yahoo ↔ NHL ID mapping)
- [x] Frontend dashboard (monitoring UI)
- [x] Transaction evaluator core (`src/optimize/`) — see Phase 1 status below

## What We Need to Build

### Phase 1: Transaction Evaluator (IN PROGRESS — structurally complete, needs live tuning)
- [x] FPTS projection per remaining schedule (slot-aware weekly + ROS)
- [x] Drop cost calculator (with position scarcity)
- [x] Net gain scoring function (aggression-weighted weekly vs ROS)
- [x] Roster flexibility analysis (position scarcity per roster)
- [x] Goalie streaming evaluator (real shot_attempts data, deterministic start prediction)
- [x] Weekly optimizer (greedy forward search with look-ahead)
- [x] Upside model (shooting luck, TOI trend, process metrics) — untested
- [x] Desperation metric (aggression level from matchup context) — untested
- [x] Backtest harness — coded, needs historical roster snapshots to run
- [ ] Wire goalies into unified `recommend()` candidate pool (skaters + goalies compete together)
- [ ] Port upside model to `GameAdvancedStats` (currently reads NST, no 2025-26 data)
- [ ] End-to-end validation of `recommend()` against live Yahoo FA pool
- [ ] Backtest: simulate transactions on 2024-25 season

### Phase 2: Agent Loop (pre-season)
- [ ] Background polling daemon
- [ ] Event detection (injury, goalie confirm, line change, hot/cold)
- [ ] Decision engine (auto-execute vs queue vs ignore)
- [ ] Transaction log table
- [ ] Yahoo write API (add/drop execution)

### Phase 3: Lineup Optimizer (pre-season)
- [ ] Daily lineup projection
- [ ] Slot assignment with FPTS maximization
- [ ] Yahoo lineup-set API
- [ ] GTD (game-time decision) handling

### Phase 4: Live Season (October 2026)
- [ ] Run agent for real
- [ ] Monitor decisions, tune thresholds
- [ ] Track performance vs league median
- [ ] Iterate on model and decision logic

### Phase 5: Trades (mid-season)
- [ ] Trade proposal generator
- [ ] Counter-offer evaluation
- [ ] Trade target identification (buy low on injured stars returning)

## Success Metrics

- **Transaction edge**: FPTS gained from pickups vs league average
- **Lineup efficiency**: % of optimal lineup set correctly
- **Reaction time**: minutes between trigger event and transaction
- **Win rate**: finish top 3 in a 16-team league
- **Season goal**: championship

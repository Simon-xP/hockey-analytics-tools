# Task List

Development tasks for PuckAgent — autonomous fantasy hockey agent.
See `docs/autonomous-agent.md` for the full design.

## Completed

### Foundation
- [x] PostgreSQL database with SQLAlchemy models
- [x] NHL API client — seed teams, players, rosters, game scores
- [x] Player resolver with name normalization
- [x] Yahoo ↔ NHL player ID mapping (yahoo_player_id, yahoo_positions)

### Data Ingestion
- [x] NST season stats scraper (2019-2025 historical)
- [x] NST game log scraper (individual per-60 rates, 2023-24 and 2024-25)
- [x] Game scores backfill from NHL API (2023-24, 2024-25, 2025-26)
- [x] NHL API play-by-play events and shifts ingestion (2015-2026)
- [x] On-ice advanced stats derived from shift-event correlation (CF, CA, xGF, xGA, HDCF, zone starts)
- [x] Schedule loader from CSV
- [x] Daily Faceoff scraper (goalie starts with confirmation, line combos, injuries)

### xG Model
- [x] Shot-level expected goals model (XGBClassifier per strength state)
- [x] 30 features: distance, angle, shot type, game state, sequence (rebound/rush/flurry)
- [x] AUC 0.833 on 2025-26 holdout, well-calibrated (pred 0.053 vs actual 0.053)
- [x] Daily pipeline: events → shot_attempts → xG scoring

### Forecasting
- [x] v2 situation-split models (5v5/PP regression, PK Poisson, Other empirical Bayes)
- [x] Per-stat feature filtering (goals:93, assists:94, shots:72, hits:20, blocks:57)
- [x] 4-season training (2021-22 through 2024-25), walk-forward backtest on 2025-26
- [x] FPTS MAE 1.630, correlation 0.425 on 39k predictions
- [x] Non-overlapping rolling windows (L5, L6-15, L16-30) + season avg + prior season + blended
- [x] IPP regression, opponent features, game context
- [x] Fantasy scoring weights (league-specific)
- [x] Goalie streaming score (opponent softness metric)

### Schedule Optimizer
- [x] Roster/league settings schema
- [x] Slot availability algorithm with bipartite matching
- [x] Daily flexibility report and streaming detection

### Yahoo Fantasy Integration
- [x] OAuth 2.0 auth flow with token persistence
- [x] Leagues, roster, standings, free agents, matchups
- [x] Trending players (ownership % and delta)
- [x] Streamable goalies (unrostered goalies ranked by matchup)

### Frontend & API
- [x] FastAPI backend (dashboard, players, Yahoo, news, goalie matchups)
- [x] Vite + React frontend with dark purple theme
- [x] Dashboard: scorebar, streamable goalies, roster projections, schedule outlook, optimal adds, buy low/sell high, league standings
- [x] Roster page: weekly slot availability (day/position toggle), roster table with projections, goalie section
- [x] Optimal adds: free agents ranked by FPTS/GP (Yahoo-aware)
- [x] Player search with trending players
- [x] Player detail with game log, forecast, goalie stats
- [x] Streamable goalies page with pick score, softness, opponent stats
- [x] Goalie matchup rankings page
- [x] Trade targets (buy low / sell high based on ixG regression)
- [x] TanStack Query (React Query v5) for data fetching and caching

---

## Phase 1: Transaction Evaluator (IN PROGRESS, April 2026)

Core decision engine — can this pickup improve my team?
Module: `src/tools/transactions/`. Design notes in `docs/autonomous-agent.md`.

### Done
- [x] Data models (`PlayerValue`, `TransactionCandidate`, `WeekPlan`, `ReplacementLevel`, `AggressionLevel`)
- [x] Slot-aware weekly FPTS projection (`compute_player_value`)
- [x] Simple FPTS projection for drop ranking (`compute_player_value_simple`)
- [x] Replacement level from FA pool (`compute_replacement_level`)
- [x] Drop ranking with position scarcity (`rank_drops`, `get_drop_candidates`)
- [x] Transaction scoring with aggression weighting (`score_transaction`)
- [x] Weekly optimizer (greedy forward search with look-ahead, `optimize_week`)
- [x] Goalie streaming evaluator — real stats from `shot_attempts`, deterministic start prediction (B2B aware)
- [x] Upside model — shooting luck, TOI trend, process metrics (ported to GameAdvancedStats)
- [x] Desperation/aggression metric (`compute_aggression`)
- [x] Backtest harness (coded, not run)
- [x] Live Yahoo roster loading (`load_roster_from_yahoo`, no static file)
- [x] Forecast pipeline as black box — wraps v2 model's full per-game pipeline

### Loose ends
- [ ] Wire goalies into `recommend()` candidate pool (framework exists, just needs hookup)
- [ ] End-to-end `recommend()` validation — blocked on Yahoo FA endpoint returning valid data
- [ ] Run backtest on 2024-25 — needs historical roster snapshots
- [ ] Position scarcity formula refinement (over-penalizes "tightness" on well-covered positions)
- [ ] Rookie handling — currently ignored (0 projection for no-history players). Add prospect ramp-up logic later.
- [ ] GoalieStart table population via Daily Faceoff ingestion (scraper exists but no DB insert pipeline)

## Phase 2: Agent Loop (Summer 2026)

The autonomous polling + decision + execution loop.

- [ ] Background polling daemon (2-3 min intervals)
- [ ] Event detection (injury, goalie confirm, line change, hot/cold streak)
- [ ] Decision engine (auto-execute vs queue vs ignore)
- [ ] Confidence thresholds and safety mechanisms
- [ ] Transaction log table (full audit trail with reasoning)
- [ ] Yahoo write API (add/drop execution)
- [ ] Notification system (alert owner on actions)
- [ ] Kill switch (pause agent instantly)

## Phase 3: Lineup Optimizer (Pre-season 2026)

Daily lineup setting to maximize projected FPTS.

- [ ] Daily FPTS projection per player (opponent, home/away, B2B)
- [ ] Slot assignment with FPTS maximization (bipartite matching)
- [ ] Yahoo lineup-set API
- [ ] GTD (game-time decision) handling
- [ ] Goalie start monitoring + re-optimization

## Phase 4: Live Season (October 2026)

First real test — run the agent for a full season.

- [ ] Deploy agent loop
- [ ] Monitor decisions, tune thresholds
- [ ] Track edge vs league median
- [ ] Iterate on model and decision logic

## Phase 5: Trades (Mid-season)

- [ ] Trade proposal generator
- [ ] Counter-offer evaluation
- [ ] Buy-low target identification (injured stars returning)

---

## Supporting Work

- [x] PP/SH situation-specific predictions (v2 model splits by 5v5/PP/PK/Other)
- [x] Multi-season training data (2021-22 through 2024-25)
- [x] On-ice advanced stats from NHL API play-by-play (replaced NST dependency)
- [ ] Goalie forecasting model
- [ ] Test coverage — mostly integration tests, needs unit tests for feature extractors and model logic

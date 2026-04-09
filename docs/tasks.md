# Task List

Prioritized development tasks for PuckAgent.

## Completed

### Foundation
- [x] PostgreSQL database with SQLAlchemy models
- [x] NHL API client — seed teams, players, rosters
- [x] Player resolver with name normalization

### Data Ingestion
- [x] NST season stats scraper (2019-2025 historical)
- [x] NST game log scraper (individual per-60 rates, 2023-24 and 2024-25)
- [x] Game scores backfill from NHL API (2023-24, 2024-25, 2025-26)
- [x] Schedule loader from CSV

### Forecasting
- [x] Feature extractors (rolling individual, season aggregate, game context, opponent)
- [x] OpponentExtractor with caching (GAA, GFA, rolling GAA, opponent B2B)
- [x] Baseline models (season average, weighted blend)
- [x] XGBoost model — beats baselines on all 5 stats
- [x] Walk-forward backtest harness with MAE and Poisson log-likelihood
- [x] Fantasy scoring weights (G=3, A=2, PIM=0.3, SOG=0.3, HIT=0.4, BLK=0.5)

### Schedule Optimizer
- [x] Roster/league settings schema
- [x] Slot availability algorithm with bipartite matching
- [x] Daily flexibility report and streaming detection

### Yahoo Fantasy Integration
- [x] OAuth 2.0 auth flow with token persistence
- [x] Leagues, roster, standings, free agents, matchups
- [x] Trending players (ownership % and delta)

### Frontend & API
- [x] FastAPI backend with dashboard, players, Yahoo routers
- [x] Vite + React frontend with dark purple theme
- [x] Dashboard: today's games, schedule outlook, buy low/sell high, optimal adds
- [x] Roster page: weekly slot availability, roster table with projections
- [x] Optimal adds: free agents ranked by FPTS/GP (Yahoo-aware)
- [x] Player search with trending players default view
- [x] Player detail with game log and forecast
- [x] Client-side prefetch and caching

---

## Next Up

- [ ] PP/SH situation-specific predictions (separate from all-situations)
- [ ] Daily Faceoff scraper for goalie starts
- [ ] Agent logic — confidence-based auto-pickup recommendations
- [ ] League standings widget (from Yahoo)
- [ ] News & injuries feed
- [ ] 2022-23 game log scraping (more training data)
- [ ] On-ice game logs from NST

## Backlog

- [ ] Goalie forecasting
- [ ] Trade analyzer (evaluate trade proposals)
- [ ] Deployment to production (domain, HTTPS, hosted DB)

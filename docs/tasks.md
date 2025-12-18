# Task List

Prioritized development tasks. Natural Stat Trick work is prioritized where dependencies allow.

## Phase 1: Foundation (required for everything) ✓

- [x] **Create `hockey` database and initialize tables**
  - Quick setup step
  - Needed before anything can be stored

- [x] **Build NHL API client to seed players/teams**
  - Populates the `players` and `teams` tables
  - Required for name resolution to work
  - NST data needs this to map names → NHL IDs

## Phase 2: Natural Stat Trick

- [ ] **Integrate NST scraper with player resolver**
  - Update scraper to resolve player names before storing
  - Store in main `hockey` database instead of separate `naturalstattrick` db
  - Build alias table from NST name variants

- [ ] **Add game logs scraping to NST**
  - Per-player game logs
  - Set up for nightly batch updates

- [ ] **Add derived stats from NST**
  - Season aggregations
  - Rolling stats (last 5 games, etc.)

## Phase 3: Other Data Sources

- [ ] **Daily Faceoff scraper for goalie starts**
- [ ] **Schedule data integration** (load CSV into `games` table)

## Phase 4: Tools

- [ ] **Implement schedule optimizer functions** (the stubs in `src/tools/schedule/`)

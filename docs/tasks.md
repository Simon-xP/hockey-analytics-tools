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

- [x] **Integrate NST scraper with player resolver**
  - Update scraper to resolve player names before storing
  - Store in main `hockey` database
  - Improved resolver: team fallback, 80% fuzzy threshold
  - 17,422 records across 6 seasons (2019-2025), 99.9% resolution rate

- [x] **Add rolling stats (last 5 games) to NST**
  - Replaced game logs with simpler L5 rolling stats
  - Uses same endpoint with gpfilt=gpteam and tgp=5

- [ ] **Add on-ice stats model** (optional)
  - CF/CA, FF/FA, xGF/xGA, zone starts, etc.
  - Separate from individual stats

- [ ] **Add derived stats from NST**
  - Season aggregations
  - Rolling stats (last 5 games, etc.)

## Phase 3: Other Data Sources

- [ ] **Daily Faceoff scraper for goalie starts**
- [ ] **Schedule data integration** (load CSV into `games` table)

## Phase 4: Tools

- [ ] **Implement schedule optimizer functions** (the stubs in `src/tools/schedule/`)

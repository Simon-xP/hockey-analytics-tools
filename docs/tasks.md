# Task List

Prioritized development tasks.

## Completed

### Phase 1: Foundation ✓

- [x] Create `hockey` database and initialize tables
- [x] Build NHL API client to seed players/teams (32 teams, 817+ players)

### Phase 2: Natural Stat Trick ✓

- [x] Integrate NST scraper with player resolver (17,422 records, 99.9% resolution)
- [x] Add rolling stats (L5 games)
- [x] Add on-ice stats model (CF/CA, FF/FA, xGF/xGA, etc.)

---

## Current Focus: Fantasy Schedule Optimizer

See [docs/schedule-optimizer.md](schedule-optimizer.md) for detailed requirements.

### Part 1: Schedule & Roster Flexibility

- [ ] **Load schedule into database**
  - Parse `data/raw/nhl-schedule-raw.csv` into `games` table
  - Index by date and team for fast lookups

- [ ] **Define roster/league settings schema**
  - League settings: slots per position (C, LW, RW, D, G, UTIL)
  - Roster: list of players with their positional eligibility
  - Support multi-position eligibility (C/LW, LW/RW, etc.)

- [ ] **Build roster input system**
  - Input current roster (player names/IDs + positions)
  - Allow swaps for add/drop transactions
  - Persist roster config (JSON or DB)

- [ ] **Implement slot availability algorithm**
  - For each day: determine which players are playing
  - Calculate used vs available slots per position
  - Handle UTIL slot (any F or D)
  - Handle multi-position eligibility optimally

- [ ] **Build daily flexibility report**
  - Output: which positions have open slots per day
  - Identify "tight" days (near capacity) vs "flexible" days
  - Suggest which positions to stream

### Part 2: Fantasy Point Predictions

- [ ] **Define fantasy scoring system**
  - Map stats to point values (G, A, +/-, SOG, PPP, etc.)
  - Support custom league scoring

- [ ] **Build player projection model**
  - Inputs: season stats, L5 rolling stats, on-ice metrics
  - Consider opponent quality (goals against, save %)
  - Consider goalie matchup if available

- [ ] **Weekly/daily projection output**
  - Projected points per player per game
  - Aggregate weekly projections

---

## Backlog

- [ ] Daily Faceoff scraper for goalie starts
- [ ] Yahoo Fantasy API integration (roster sync)
- [ ] Streaming recommendations engine

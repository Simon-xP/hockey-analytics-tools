# Development Journal

A sequential log of development decisions, what was built, and why.

---

## Entry 1: Project Assessment and Planning

**Date:** 2025-12-01

### Starting Point

Created a loose collection of files:
- `natural-stat-trick-scraper/` - Scraper for NST stats, storing to PostgreSQL
- `natural-stat-trick-eda/` - Jupyter notebook for exploration
- `nhl-schedule/` - Schedule CSV and stub functions for fantasy optimization

The schedule-api.py had AI-generated function stubs that weren't connected to any database.

### Problem Identified

Multiple data sources would need to reference players, but:
- Different sources use different name formats ("Alex Ovechkin" vs "A. Ovechkin" vs "Ovechkin, Alex")
- Some names are ambiguous (two "Sebastian Aho" players exist)
- No central player database to join data across sources

### Decision: Entity Resolution First

Before building more tools, we need a **player resolution layer** that:
1. Maintains a canonical player table (using NHL's official player IDs)
2. Stores known name aliases per data source
3. Provides fuzzy matching for new/unknown name formats
4. Uses team/position as a disambiguation key when names collide

This becomes the foundation everything else builds on.

---

## Entry 2: Architecture Design

**Date:** 2025-12-01

### Options Considered

1. **Flat package structure** - Simple, everything in one `hockey/` package
2. **Domain-separated structure** - Layers with clear boundaries (`core/`, `ingest/`, `tools/`)

### Decision: Domain-Separated (Option 2)

Chose the more scalable structure because:
- Will have many data sources (NHL API, NST, Daily Faceoff, Twitter, etc.)
- Each source has different update patterns and requirements
- Want to be able to modify one source without touching others
- Clear separation makes it easier to understand what depends on what

### Directory Structure Created

```
src/
├── core/           # Foundation (models, resolver, db)
├── ingest/         # Data sources (one subdir per source)
└── tools/          # User-facing functionality
```

Plus supporting directories: `config/`, `data/`, `docs/`, `scripts/`, `tests/`, `notebooks/`

---

## Entry 3: Core Layer Implementation

**Date:** 2025-12-16

### Database Models Created

**`src/core/models/`**

| Model | Purpose |
|-------|---------|
| `Team` | NHL teams with ID, abbreviation, full name |
| `Player` | Players with NHL ID, name, team, position |
| `PlayerAlias` | Name variations per source for resolution |
| `Game` | Schedule entries with home/away teams, dates |
| `GoalieStart` | Confirmed/projected goalie starts per game |

Key design decisions:
- Use NHL's official IDs as primary keys (stable, authoritative)
- Store `normalized_name` for fast lookups (lowercase, no punctuation, sorted words)
- Track `source` on aliases to know where each variation came from

### Player Resolver Created

**`src/core/resolver/`**

Three-file structure:
- `normalize.py` - Name normalization functions
- `matcher.py` - Matching strategies (exact, fuzzy)
- `__init__.py` - Main `resolve_player()` entry point

**Normalization logic:**
```python
"O'Reilly, Ryan" → "oreilly ryan"  # Remove punctuation, sort words
"Nikolaj Ehlers" → "ehlers nikolaj"  # Alphabetical sort
"Höglander"      → "hoglander"       # Remove accents
```

**Matching priority:**
1. Exact match on NHL ID (if provided)
2. Exact match on known alias
3. Exact match on normalized name
4. Fuzzy match (using rapidfuzz library)
5. Fuzzy match + team for disambiguation

### Database Connection

**`src/core/db.py`**

- Uses SQLAlchemy with PostgreSQL
- Connection string from environment variable or default
- Context manager for session handling with auto-commit/rollback

---

## Entry 4: Migration of Existing Code

**Date:** 2025-12-16

### Files Moved

| From | To |
|------|-----|
| `natural-stat-trick-scraper/scrape_natural_stat_trick.py` | `src/ingest/natural_stat_trick/scraper.py` |
| `natural-stat-trick-scraper/natural_stat_trick_config.json` | `src/ingest/natural_stat_trick/config.json` |
| `nhl-schedule/schedule-api.py` | `src/tools/schedule/optimizer.py` |
| `nhl-schedule/schedule-data/nhl-schedule-raw.csv` | `data/raw/nhl-schedule-raw.csv` |
| `natural-stat-trick-eda/*.ipynb` | `notebooks/` |

### Updates Made

- Fixed config file paths to use `pathlib` relative to script location
- Updated shell script to use `python -m` module syntax
- Old directories removed after migration

---

## Entry 5: Package Configuration

**Date:** 2025-12-16

### pyproject.toml Created

Modern Python packaging using `pyproject.toml`:
- Defines dependencies (pandas, sqlalchemy, rapidfuzz, etc.)
- Optional dev dependencies (pytest, black, ruff)
- Configures code formatting tools

### Dependencies

| Package | Purpose |
|---------|---------|
| pandas | Data manipulation |
| sqlalchemy | ORM for database |
| psycopg2-binary | PostgreSQL driver |
| rapidfuzz | Fuzzy string matching |
| unidecode | Accent/unicode normalization |
| requests | HTTP client |
| beautifulsoup4 | HTML parsing for scrapers |

---

## Entry 6: NHL API Client and Database Seeding

**Date:** 2025-12-17

### Database Setup

- Created `hockey` PostgreSQL database
- Initialized tables via `init_db()` (teams, players, player_aliases, games, goalie_starts)
- Set up virtual environment (`hockey-venv`) with all dependencies

### NHL API Client Built

**`src/ingest/nhl_api/`**

| File | Purpose |
|------|---------|
| `client.py` | Functions to fetch from NHL's public APIs |
| `seed.py` | Populates database with teams and players |

**API endpoints used:**
- `api-web.nhle.com/v1/standings/now` - Get current teams
- `api-web.nhle.com/v1/roster/{team}/current` - Get team rosters
- `api.nhle.com/stats/rest/en/team` - Get team IDs

**Rate limiting:** Added 0.5s delay between requests to avoid 429 errors.

### Data Seeded

- **32 teams** - All current NHL teams with IDs, abbreviations, names
- **817 players** - All active roster players with NHL IDs, positions, team links

### Resolver Tested

```python
resolve_player(session, name="Nathan MacKinnon")      # → 8477492
resolve_player(session, name="Nate MacKinnon")        # → 8477492 (fuzzy)
resolve_player(session, name="Sebastian Aho", team_abbrev="CAR")  # → 8478427
```

### Schema Pattern

Using a snowflake schema approach:
- **Dimension tables:** `players`, `teams`, `games` (descriptive data)
- **Fact tables:** `goalie_starts`, future `game_logs` (measurable events)

Players table acts as the central dimension that all stats data joins to.

---

## Entry 7: Natural Stat Trick Integration

**Date:** 2025-12-18

### Season Stats Scraper

Integrated NST scraper with player resolution:

**Scraper Updates:**
- Refactored scraper with clear sections: shared utilities, season stats, game logs (TODO), entry points
- Two modes: `--historical` (2019-2024) and `--current` (2024-2025)
- 10-second delay between requests to avoid rate limiting
- NST team abbreviation mapping (S.J → SJS, L.A → LAK, etc.)

**Player Resolution Improvements:**
- NHL API `/roster/{team}/current` only returns active roster (misses IR players)
- Added `/roster/{team}/{season}` endpoint for full season roster
- Added `get_skaters_with_games()` to catch AHL callups who played NHL games
- Lowered fuzzy threshold from 85% → 80% (catches Zack/Zachary, Max/Maxwell, Alex/Alexander)
- Added team fallback: tries fuzzy match with team filter, then without (handles trades)

**Results:**
- 1,092 players in database (rosters + stats API)
- 17,422 season stat records across 6 seasons (2019-2025)
- 99.9% resolution rate (919/920 current season players)
- Only 1 unresolved: Pierre-Olivier Joseph (NST: "Pierre-Olivier" vs DB: "P.O")

**Individual Stats Situations Scraped:**
1. 5v5_individual_counts
2. 5v5_individual_rates
3. all_individual_counts
4. all_individual_rates
5. pp_individual_counts

On-ice stats (CF, CA, xGF, xGA, etc.) skipped for now - different columns, would need separate model.

---

## Entry 8: Fantasy Schedule Optimizer Project

**Date:** 2025-12-18

### New Goal

Building a fantasy hockey schedule optimizer for Yahoo Fantasy (points-based, daily lineups). Two main components:

1. **Schedule & Roster Flexibility** - Which days have open slots, which positions can be streamed
2. **Fantasy Point Predictions** - Project player output for streaming decisions

### The Core Problem

In daily lineup leagues, you need to maximize games played. But roster construction is constrained by:
- Position slots (2C, 2LW, 2RW, 4D, 2G, 2UTIL)
- Multi-position eligibility (C/LW, LW/RW players)
- Team schedules (some days have more games)

The challenge: given your roster and league settings, identify which days have flexibility to stream additional players, and at which positions.

### Technical Approach

Multi-position eligibility creates an **assignment problem**. A player eligible for C/LW affects both position pools. Plan to use bipartite matching to optimally assign players to slots, then identify remaining capacity.

### Data Already Available

- NHL schedule CSV with dates, teams, opponents, Yahoo week numbers
- Player database with NHL IDs, teams, positions
- NST stats for projection work later

### What's Missing

- Roster input system (player + Yahoo positional eligibility)
- League settings configuration
- Schedule loaded into database for queries
- The slot availability algorithm itself

### Decision: Start with Schedule

Focusing on Part 1 (schedule flexibility) before Part 2 (point predictions). The prediction algorithm needs more research on which factors matter most.

See [docs/schedule-optimizer.md](schedule-optimizer.md) for full requirements.

---

## Entry N: Transaction Evaluator (Phase 1 of PuckAgent)

**Date:** 2026-04-12

### Goal

Build the decision engine that answers "should I add this player?" Treat
the forecast model as a black box input and focus on the infrastructure
around it: slot-aware valuation, drop ranking, goalie streaming, weekly
optimization under a 4-add constraint.

### What was built

Module: `src/tools/transactions/` (10 files, ~80KB):
- `models.py` — `PlayerValue`, `TransactionCandidate`, `WeekPlan`, `AggressionLevel`, `ReplacementLevel`, `GoalieStreamScore`
- `player_value.py` — slot-aware weekly FPTS projection (calls the v2 forecast per game) + live Yahoo roster loader
- `replacement_level.py` — top-5 avg of FA pool per position group
- `drop_ranker.py` — rank roster players by droppability (weekly value + position scarcity + upside bonus)
- `weekly_optimizer.py` — transaction scoring + greedy forward search with look-ahead deferral
- `goalie_eval.py` — derives goalie stats from `shot_attempts` (saves, GA, wins, shutouts), deterministic start prediction (starter vs committee vs backup, skips B2B second nights)
- `upside.py` — shooting luck / TOI trend / process metrics (currently reads NST, needs port)
- `desperation.py` — matchup context → `AggressionLevel` (shifts weekly vs ROS weighting)
- `backtest.py` — walk-forward transaction simulator (coded but not run)
- `__init__.py` — public API: `recommend()`, `evaluate_add()`, `load_roster_from_yahoo()`

### Key design decisions

**Forecast as black box.** The evaluator wraps the v2 situation-split
model via `_default_forecast_fn(nhl_id, game_date, avg_toi) -> float`.
This calls `extract_all_features`, predicts per-60 rates per situation
(5v5/PP/PK/Other), predicts TOI per situation, then `project_per_game`
combines into per-game fantasy points with PP/SH bonuses. The evaluator
doesn't know about any of this internally — it just gets FPTS back.
This keeps forecast iteration independent from evaluator iteration.

**Slot-aware for adds, simple for drops.** When evaluating a free agent,
we check if they'd actually make the active lineup on each game day
(`analyze_week` + `assign_players_to_slots`) and only count games where
they fit. When evaluating a rostered player for dropping, we use
historical FPTS/GP × all games — because dropping frees a slot, so the
full value is what's lost. If we used slot-aware logic for drops, a
bench-blocked player would look like 0 value and we'd always drop them.

**Goalies are deterministic, not probabilistic.** An earlier approach
multiplied goalie FPTS by crease share, so a 67% starter got 67% of
his output on every game. This was philosophically wrong — goalies
either start or they don't. Replaced with `predict_starts()`: for each
game this week, binary yes/no. Starters (≥60% crease share) play
everything except B2B second nights. Committee/backup goalies are
assumed to not start without explicit confirmation.

**Goalie stats from shot_attempts.** No dedicated goalie game-stats
table. Every shot has `goalie_id`, so we compute per-game saves (shots
where `is_goal=false`), GA (`is_goal=true`), shots against (total), wins
(from Game scores), shutouts (GA=0), and FPTS from `GOALIE_WEIGHTS`.
Opponent softness is 60/40 blended with goalie's own rate.

**Live Yahoo, no static config.** Earlier iteration used
`config/roster.json`. Removed in favor of `load_roster_from_yahoo()`
which calls `get_my_team()` live. Filters out IR/NA players.

**Rookies deferred.** Players with no significant historical data
return 0 projections. We don't draft rookies in this league anyway.
Later work: add prospect handling (projected TOI ramp-up, comparable-
player matching).

### Loose ends

- End-to-end `recommend()` validation blocked on Yahoo FA endpoint
  returning retired/NA players (separate workstream)
- Upside model reads `GameIndividualStats` (NST, no 2025-26 data),
  needs port to `GameAdvancedStats`
- Desperation metric untested — needs opponent roster projection
- Backtest never run — needs historical roster snapshots
- Goalies not yet wired into `recommend()` candidate pool (framework
  exists, just needs the plumbing)
- `compute_position_scarcity` over-penalizes tight-but-covered positions

### Iterations that happened during testing

1. **Goalies getting absurd numbers** from skater forecast — added
   `player_type == GOALIE` early return in both valuation paths.
2. **Drop ranking showing M.Tkachuk at 0 weekly FPTS** because he was
   slot-blocked on all game days. Fix: drop ranker uses simple (non-slot-
   aware) valuation.
3. **Drop ranking using 2024-25 data** because `compute_fpts_per_gp`
   only read NST. Ported to use `GameAdvancedStats` first for 2025-26,
   with NST fallback for older seasons.
4. **V2 forecast hallucinating huge numbers for retired players**
   (Suter 37, Vlasic 33, Pacioretty 31). Fixed upstream in the v2 model
   — now returns 0 or sensible small numbers for sparse-data players.
5. **Goalie probabilistic discount was wrong** — replaced with binary
   start prediction based on crease share + B2B detection.

## Entry 10: Forecasting v2 — Non-overlapping Windows + 5-season Retrain

**Date:** 2026-04-15

### Problem

Top-tier skaters (MacKinnon, Kucherov, Pastrnak, Kaprizov, McDavid) were
systematically projected 2–3 fpts **above** their season averages for
single-game forecasts, which inflated add/drop recommendations for hot
streakers and flagged elites as must-haves even when they were cold.

Root cause: rolling features used overlapping EWMA half-lives (5/10/15
games), so the last 5 games contributed to all three windows. XGBoost
latched onto recent form and compounded it across features.

### Changes

1. **Non-overlapping rolling windows.** Replaced EWMA half-lives with
   disjoint windows `L5` (games 0–4), `L6_15` (5–14), `L16_30` (15–29).
   Each game contributes to exactly one window. Season average remains
   as the long-term anchor; `prior_*` / `blended_*` handle cold-start.
   See `src/tools/forecasting/v2/constants.py:ROLLING_WINDOWS`.

2. **5v5 Empirical Bayes blend for goals/assists.** Extended
   `blend_xgb_with_eb` with an `only_stats` parameter so 5v5 goals and
   assists get credibility-weighted toward a prior, but hits/blocks/shots
   stay pure XGB. Cuts variance for players with sparse 5v5 scoring
   samples without affecting the high-volume stats.

3. **5-season retrain.** Ingested 2021–22 (the only missing
   post-COVID season) and retrained 5v5 + PP on
   `20212022 / 20222023 / 20232024 / 20242025 / 20252026`. 5v5 went from
   38k → 221k training samples, PP from 20k → 128k.

### Results

Star comparison (projection vs season avg, 2026-04-15):

| Player    | Before | After |
|-----------|--------|-------|
| McDavid   | +2.76  | +1.23 |
| MacKinnon | +1.94  | -0.84 |
| Kucherov  | +1.58  | -1.14 |
| Pastrnak  | +1.22  | -0.31 |
| Kaprizov  | +1.40  | -0.32 |

Upward bias on elites is gone. Most stars now sit slightly below season
average (expected: season avg includes peak games, single-game projection
regresses toward true talent). No calibration pass needed.

Feature importance on the 5-season model leans much harder on stable
signals: `is_forward` (0.457), `ipp_regressed`, `blended_goals`,
`blended_shots`, `season_avg_*`. Recent-window features still appear
but no longer dominate.

### No data leakage

Verified `load_player_game_stats` uses `g.date < :before_date` — strict
prior-games-only filter. Walk-forward training extracts features at each
historical date using only games before that date. Season-level
aggregates (`season_avg_*`, `prior_*`) are computed game-by-game, not
from end-of-season totals.

### How to use

Same public API — the change is transparent to callers:

```python
from src.tools.forecasting.v2.forecast import load_models, forecast_player
from src.tools.forecasting.v2.toi_model import TOIPredictor
from src.tools.forecasting.v2.empirical_bayes import EmpiricalBayesPredictor

models = load_models()
toi = TOIPredictor()
eb_pp = EmpiricalBayesPredictor("pp", ["goals", "assists", "shots"])
eb_pk = EmpiricalBayesPredictor("pk", ["goals", "assists"])
eb_5v5 = EmpiricalBayesPredictor("5v5", ["goals", "assists"])  # NEW

proj = forecast_player(
    session, nhl_id, game_date,
    models=models, toi_predictor=toi,
    eb_pp=eb_pp, eb_pk=eb_pk, eb_5v5=eb_5v5,  # eb_5v5 is new
)
# proj["fpts"] — single-game fantasy point projection
```

Transaction evaluator already calls this through `_default_forecast_fn`
so no changes needed downstream — just retraining the model propagates
the improvement.

## Lessons Learned

1. **Entity resolution is foundational** - Get this right first, everything else becomes easier
2. **Separate by domain, not by file type** - Keep related code together (scraper + config + docs per source)
3. **Use canonical IDs from authoritative sources** - NHL's player IDs are stable; don't invent your own
4. **Normalize early, match consistently** - All name comparisons go through the same normalization
5. **Test infrastructure against real data early** - Several design issues (goalies in skater pipeline, slot-aware drops, probabilistic goalies) only surfaced when running against the actual roster.
6. **Keep the forecast model and the decision engine independent** - Letting them iterate on different timelines means each can be improved without rewriting the other.

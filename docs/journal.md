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

## Next Steps

- [ ] Integrate NST scraper with player resolver
- [ ] Add game logs scraping to NST
- [ ] Daily Faceoff scraper for goalie starts
- [ ] Schedule data integration

---

## Lessons Learned

1. **Entity resolution is foundational** - Get this right first, everything else becomes easier
2. **Separate by domain, not by file type** - Keep related code together (scraper + config + docs per source)
3. **Use canonical IDs from authoritative sources** - NHL's player IDs are stable; don't invent your own
4. **Normalize early, match consistently** - All name comparisons go through the same normalization

# Project Architecture

## Overview

This project uses a **layered architecture** with a **src layout**. The goal is to keep code organized, maintainable, and easy to extend as new data sources and tools are added.

## Architecture Pattern

Everything this project does falls into one of three stages: **get the data in**, **predict what happens next**, **decide what to do about it**.
The directory layout mirrors those stages directly, with a foundation layer underneath and a delivery layer on top.

```
┌─────────────────────────────────────────────────────────┐
│                     FRONTEND                            │
│         Vite + React, TanStack Query (v5)               │
│                   frontend/                             │
└─────────────────────────┬───────────────────────────────┘
                          │ HTTPS
┌─────────────────────────▼───────────────────────────────┐
│                       API                               │
│     FastAPI, Pydantic response models, httpx            │
│                   src/api/                              │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│                     OPTIMIZE                            │
│   Decide. Lineup slots, player value, drop ranking,     │
│   week optimization, matchup state.                     │
│                  src/optimize/                          │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│                      PREDICT                            │
│   Forecast. Situation-split per-60 + TOI models,        │
│   upside and opportunity signals.                       │
│                  src/predict/                           │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│                     ANALYTICS                           │
│   Derive. Advanced stats from shifts/events, xG, RAPM.  │
│                 src/analytics/                          │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│                      INGEST                             │
│   Get data in. NHL API, NST, Yahoo, news, schedule.     │
│                  src/ingest/                            │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│                       CORE                              │
│   Models, resolver, DB, scoring weights, as_of queries. │
│                   src/core/                             │
└─────────────────────────────────────────────────────────┘

              src/backtest/  — replays historical decisions
              against src/optimize strategies. Sits beside
              the stack, not inside it.
```

**Key principle:** each layer only depends on the layers below it.
A module that needs to reach *upward* is a sign it is in the wrong place.

## Directory Structure

```
hockey-analytics-tools/
├── alembic/         # Database migrations (Alembic)
├── config/          # Configuration (database URLs, API keys, SSL certs)
├── data/            # Raw data files (CSVs, cached responses)
│   └── raw/         # Immutable source files
├── docs/            # Documentation and development journal
├── frontend/        # Vite + React frontend
│   └── src/
│       ├── api/     # API client (fetchJSON wrappers)
│       ├── components/  # Shared UI components
│       └── pages/   # Route pages (each uses TanStack Query)
├── models/          # Trained ML model artifacts (.pkl)
├── notebooks/       # Jupyter notebooks for exploration
├── scripts/         # Entry points, cron jobs, one-off scripts
├── src/             # Main Python package
│   ├── core/        # Foundation: models, resolver, db, scoring, queries
│   ├── ingest/      # Get data in — one subdirectory per source
│   ├── analytics/   # Derive metrics from raw data (advanced_stats, xg, rapm)
│   ├── predict/     # Forecast player performance (forecasting, signals)
│   ├── optimize/    # Decide what to do (slots, value, week, matchup)
│   ├── backtest/    # Replay historical decisions
│   └── api/         # FastAPI app, routers, Pydantic schemas
└── tests/           # Test files, mirroring src/
```

## Layer Details

### Core Layer (`src/core/`)

The foundation that everything else builds on. Contains:

- **models/** - SQLAlchemy ORM models (Player, Team, Game, etc.)
- **resolver/** - Player name resolution system
- **db.py** - Database connection and session management

**Why separate?** These are stable, rarely-changing components. If you switch from PostgreSQL to another database, only this layer changes.

### Ingest Layer (`src/ingest/`)

Data ingestion from external sources. Each source gets its own subdirectory:

- **nhl_api/** - Official NHL API client
- **natural_stat_trick/** - Web scraper for NST stats
- **daily_faceoff/** - Goalie start scraper

**Why separate directories per source?** Each source has different:
- Authentication requirements
- Rate limits
- Data formats
- Update frequencies

Keeping them isolated makes it easy to modify one without affecting others.

### Analytics Layer (`src/analytics/`)

Derived metrics computed from raw ingested data. Not predictions, not decisions — these turn events and shifts into the enriched stats everything above reads.

- **advanced_stats/** - Shift-event correlation engine that builds `GameAdvancedStats` from raw events
- **xg/** - Expected goals model (shot-level XGBoost from NHL API play-by-play)
- **rapm/** - Regularized adjusted plus-minus player ratings

**Why not in ingest?** Ingest writes what a source gave us. Analytics writes what we computed. When a model changes we re-derive; we do not re-scrape.

### Predict Layer (`src/predict/`)

What will a player do next.

- **forecasting/** - Situation-split model (5v5/PP/PK/Other). Uses `GameAdvancedStats` from the NHL API pipeline. Predicts per-60 rates + TOI per situation, combines into per-game fantasy points with PP/SH bonuses.
- **signals/** - `upside` (individual talent ceiling) and `opportunity` (situational favorability), both scored on `[-1.0, 1.0]`.

Knows nothing about rosters, leagues, or transactions. A forecast for a player is the same number whether or not you own them.

### Optimize Layer (`src/optimize/`)

What to do about it. The only layer that knows fantasy teams exist.

- **models/** - Dataclasses grouped by concern: `roster`, `value`, `plan`, `matchup`
- **slots.py** - Bipartite matching slot assignment: who makes the active lineup today
- **value.py**, **replacement.py**, **drops.py**, **goalies.py** - What a player is worth, and what he is worth relative to the pool
- **injuries.py** - Injury reports → expected games missed
- **week/** - `optimize_week()` for *any* team: `heavy` for my own (pickup-level search, aggression-aware), `light` for opponents (roster projection + best add path, cheaper)
- **matchup/** - Two projections → win probability → aggression level, which feeds back into `heavy`

**Why one entry point for any team?** Knowing how many points an opponent can put up matters as much as knowing your own ceiling. Modelling them with a different algorithm makes the two numbers incomparable.

## Key Concepts

### Entity Resolution

The most important architectural decision: **all data sources resolve to canonical NHL player IDs**.

```
[Yahoo name] ──┐
[NST name]   ──┼──> resolve_player() ──> nhl_player_id
[Tweet]      ──┘
```

This happens in `src/core/resolver/`. The `player_aliases` table stores known name variations per source.

### Configuration

Database URLs and API keys live in `config/settings.py`, loaded from environment variables with sensible defaults. This keeps secrets out of code.

### Scripts vs Modules

- **scripts/** - One-off or scheduled entry points (run from command line)
- **src/** - Importable modules with functions (used by scripts, the API, or notebooks)

A script should be a thin argument parser over a module function. If logic lives only in `scripts/`, the API and the backtest cannot reach it.

### Temporal Gating (`src/core/queries/`)

Every historical read takes an `as_of` date and uses a strict `Game.date < as_of` cutoff, so a decision made on day D cannot see day D's own games.

Live code passes today; backtests pass the simulated decision date. Same code path either way — that shared path is the only reliable defense against time leakage, which is why these queries live in `core` rather than in `backtest`.

### API Layer (`src/api/`)

FastAPI backend serving the frontend over HTTPS (required for Yahoo OAuth).

- **routers/** — Route handlers organized by domain: `dashboard.py`, `players.py`, `yahoo.py`, `news.py`, `goalie_matchups.py`
- **schemas.py** — Pydantic response models for all endpoints. Every route has a `response_model=` for typed validation and auto-generated OpenAPI docs at `/docs`
- FPTS computation is shared with the rest of the stack via `src/core/queries/stats_helpers.py`

All HTTP calls within route handlers use `httpx.AsyncClient` to avoid blocking the event loop.

### Frontend Layer (`frontend/`)

Vite + React SPA. Dark theme with a glassmorphism aesthetic.

- **Data fetching**: TanStack Query (React Query v5) with `staleTime: 10min`, `gcTime: 30min`, `refetchOnWindowFocus: false`. Conditional Yahoo queries use the `enabled` option.
- **API client** (`src/api/client.js`): Thin wrappers around `fetch()` — one exported function per endpoint. TanStack Query handles caching and deduplication.
- **Routing**: React Router v6 with a shared `Layout` component. 11 route pages.

### Database Migrations (`alembic/`)

Alembic manages schema migrations. Configured in `alembic/env.py` to read `DATABASE_URL` from `config/settings.py` and `Base.metadata` from `src/core/models`. Run `alembic upgrade head` to apply, `alembic revision --autogenerate -m "description"` to generate.

## Common Patterns

### Adding a New Data Source

1. Create `src/ingest/new_source/`
2. Add `__init__.py`, `client.py` (or `scraper.py`), `config.json`
3. Use `resolve_player()` to map names to NHL IDs before storing
4. Add a script in `scripts/` to run it

### Adding New Functionality

Pick the layer by asking what the code *does*, not what feature it serves:

| It... | Goes in |
|-------|---------|
| fetches from an external source | `src/ingest/` |
| computes a metric from data we already have | `src/analytics/` |
| estimates something that has not happened yet | `src/predict/` |
| chooses between options for a fantasy team | `src/optimize/` |

Then import only from layers below. If you find yourself importing upward, the code is in the wrong layer.

## References

- [Python Packaging: src layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
- [Clean Architecture](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)
- [Domain-Driven Design](https://martinfowler.com/bliki/DomainDrivenDesign.html)

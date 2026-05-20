# Project Architecture

## Overview

This project uses a **layered architecture** with a **src layout**. The goal is to keep code organized, maintainable, and easy to extend as new data sources and tools are added.

## Architecture Pattern

```
┌─────────────────────────────────────────────────────────┐
│                     FRONTEND                             │
│         Vite + React, TanStack Query (v5)               │
│                   frontend/                             │
└─────────────────────────┬───────────────────────────────┘
                          │ HTTPS
                          ▼
┌─────────────────────────────────────────────────────────┐
│                       API                                │
│     FastAPI, Pydantic response models, httpx             │
│                   src/api/                              │
└─────────────────────────┬───────────────────────────────┘
                          │ uses
                          ▼
┌─────────────────────────────────────────────────────────┐
│                      TOOLS                               │
│         (schedule optimizer, projections, etc.)          │
│                  src/tools/                              │
└─────────────────────────┬───────────────────────────────┘
                          │ uses
                          ▼
┌─────────────────────────────────────────────────────────┐
│                      INGEST                              │
│     (NHL API, Natural Stat Trick, Daily Faceoff)        │
│                  src/ingest/                             │
└─────────────────────────┬───────────────────────────────┘
                          │ uses
                          ▼
┌─────────────────────────────────────────────────────────┐
│                       CORE                               │
│        (models, resolver, database connection)           │
│                   src/core/                              │
└─────────────────────────────────────────────────────────┘
```

**Key principle:** Each layer only depends on layers below it. Frontend calls the API over HTTPS. API uses tools and ingest. Tools use ingest and core. Ingest uses core. Core depends on nothing else in the project.

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
│   ├── api/         # FastAPI app, routers, Pydantic schemas
│   ├── core/        # Foundation layer
│   ├── ingest/      # Data ingestion layer (uses httpx for HTTP)
│   └── tools/       # Application layer
└── tests/           # Test files
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

### Tools Layer (`src/tools/`)

User-facing functionality that consumes data:

- **schedule/** - Schedule optimization for fantasy hockey (slot availability, bipartite matching)
- **forecasting/** - Legacy v1 XGBoost forecast model (NST data)
- **forecasting/v2/** - Situation-split forecast model (5v5/PP/PK/Other). Uses `GameAdvancedStats` from our NHL API pipeline. Predicts per-60 rates + TOI per situation, combines into per-game fantasy points with PP/SH bonuses.
- **xg/** - Expected goals model (shot-level XGBoost from NHL API play-by-play)
- **advanced_stats/** - Shift-event correlation engine that builds `GameAdvancedStats` from raw events
- **fantasy/** - League scoring weights and FPTS projection
- **transactions/** - Transaction evaluator (Phase 1 of PuckAgent). Consumes the v2 forecast as a black box and makes add/drop decisions with slot-aware projections, drop ranking, goalie streaming, and a greedy weekly optimizer.

**Why separate from ingest?** Ingest is about getting data in. Tools are about using data. Different concerns, different change patterns.

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

### Scripts vs Tools

- **scripts/** - One-off or scheduled entry points (run from command line)
- **src/tools/** - Importable modules with functions (used by scripts or notebooks)

### API Layer (`src/api/`)

FastAPI backend serving the frontend over HTTPS (required for Yahoo OAuth).

- **routers/** — Route handlers organized by domain: `dashboard.py`, `players.py`, `yahoo.py`, `news.py`, `goalie_matchups.py`
- **schemas.py** — Pydantic response models for all endpoints. Every route has a `response_model=` for typed validation and auto-generated OpenAPI docs at `/docs`
- **stats_helpers.py** — Shared FPTS computation used by multiple routers

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

### Adding a New Tool

1. Create `src/tools/new_tool/`
2. Import from `src.core` for models and resolver
3. Import from `src.ingest` if you need to trigger data fetches

## References

- [Python Packaging: src layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
- [Clean Architecture](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)
- [Domain-Driven Design](https://martinfowler.com/bliki/DomainDrivenDesign.html)

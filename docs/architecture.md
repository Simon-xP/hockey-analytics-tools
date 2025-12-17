# Project Architecture

## Overview

This project uses a **layered architecture** with a **src layout**. The goal is to keep code organized, maintainable, and easy to extend as new data sources and tools are added.

## Architecture Pattern

```
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

**Key principle:** Each layer only depends on layers below it. Tools use ingest and core. Ingest uses core. Core depends on nothing else in the project.

## Directory Structure

```
hockey-analytics-tools/
├── config/          # Configuration (database URLs, API keys)
├── data/            # Raw data files (CSVs, cached responses)
│   └── raw/         # Immutable source files
├── docs/            # Documentation and development journal
├── notebooks/       # Jupyter notebooks for exploration
├── scripts/         # Entry points, cron jobs, one-off scripts
├── src/             # Main Python package
│   ├── core/        # Foundation layer
│   ├── ingest/      # Data ingestion layer
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

- **schedule/** - Schedule optimization for fantasy hockey

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

# Yahoo Free Agent Improvements

Tracked separately from the forecasting rework. These are UI/API issues with the
"Optimal Adds" feature.

## Problems

### 1. Only ~19 players shown
- Endpoint fetches at most `count=50` free agents from Yahoo API, then filters to
  those with >=15 GP in our DB. After name-matching failures and the GP filter,
  only ~19 survive.
- Goal: show **all** available free agents with projections.

### 2. Yahoo API pagination is slow
- `get_free_agents()` returns max 25 players per request.
- A 16-team league could have 500+ free agents, requiring 20+ paginated requests.
- Fetching live on every API call makes the endpoint too slow.

### 3. Projections are backward-looking
- Current ranking uses **season-average FPTS/GP** from `GameIndividualStats` (NST
  data), not actual forecasts.
- This surfaces players who peaked earlier in the season rather than players who
  are trending up or have favorable upcoming schedules.
- Fix depends on the forecasting rework (separate effort).

## Proposed Solution: Yahoo Free Agent Cache

### New table: `yahoo_free_agents`
| Column | Type | Description |
|--------|------|-------------|
| yahoo_player_key | VARCHAR PK | Yahoo's player identifier |
| nhl_id | INTEGER FK | Resolved NHL player ID (nullable if unmatched) |
| name | VARCHAR | Display name from Yahoo |
| position | VARCHAR | Position(s) from Yahoo |
| team | VARCHAR | NHL team abbreviation |
| status | VARCHAR | Healthy / IR / DTD / etc. |
| ownership_pct | FLOAT | League ownership percentage |
| last_synced | TIMESTAMP | When this row was last refreshed |

### Sync strategy
- Background job fetches **all** free agents via pagination (25 per request, ~20
  requests, ~30 seconds total).
- Run on-demand or on a schedule (e.g., every 4-6 hours, or triggered after
  roster moves).
- Resolve player names to `nhl_id` at sync time, not at query time.
- The `/optimal-adds` endpoint reads from cache instead of hitting Yahoo live.

### Benefits
- Endpoint responds instantly (DB query only).
- All free agents available, not just first 50.
- Name resolution happens once at sync time, failures can be flagged and
  manually corrected.
- Projections can be pre-computed and stored alongside cached data.

## Dependencies
- Forecasting rework (for forward-looking projections instead of season averages)
- Yahoo API client needs pagination support in `get_free_agents()`

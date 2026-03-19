# Predictive Model Features

## NST Individual Stats (current season, per-60 rates)

| Feature | DB Column | Notes |
|---------|-----------|-------|
| IPP | `ipp` | Individual points percentage |
| SH% | `sh_pct` | Shooting percentage |
| Shots/60 | `shots_per_60` | Shot volume |
| Goals/60 | `goals_per_60` | Goal scoring rate |
| ixG/60 | `ixg_per_60` | Individual expected goals — filters out shooting luck |
| iCF/60 | `icf_per_60` | Individual Corsi (all shot attempts) |
| iSCF/60 | `iscf_per_60` | Individual scoring chances |
| TOI/GP | `toi` | Ice time per game |

## NST On-Ice Stats (current season, per-60 rates)

| Feature | DB Column | Notes |
|---------|-----------|-------|
| oiSH% | `on_ice_sh_pct` | On-ice shooting percentage |
| CF/60 | `cf_per_60` | On-ice Corsi rate |
| SCF/60 | `scf_per_60` | On-ice scoring chances rate |
| xGF/60 | `xgf_per_60` | On-ice expected goals for rate |
| OZS% | `off_zone_start_pct` | Offensive zone start percentage — deployment quality |

## Opposition / Schedule (current season)

| Feature | Source | Notes |
|---------|--------|-------|
| Opponent goals against average | NHL API (team stats) | Season-long GAA |
| Opponent GAA last 5 games | NHL API (derived) | Usefulness decreases for predictions further into the future |
| Opponent starting goalie GAA/SV% | Daily Faceoff / external | **Not yet available** — goalie scraper is a stub |
| Back-to-back? | Schedule data | Bool — player's team on B2B |
| Opponent back-to-back? | Schedule data | Bool — opponent on B2B |

## Basic Historical Stats (prior season baselines)

Used as an anchor / prior, weighted less as current season progresses.

| Feature | DB Column (`SeasonStats`) | Notes |
|---------|---------------------------|-------|
| Goals/game | `goals` / GP | |
| Assists/game | `total_assists` / GP | |
| Shots/game | `shots` / GP | |
| Hits/game | `hits` / GP | |
| Blocks/game | `shots_blocked` / GP | |
| TOI/game | `toi` / GP | |
| Shooting% | `sh_pct` | |
| PIM/game | `pim` / GP | |
| +/- per game | — | **Not directly available** — could derive from on-ice GF - GA |

Combine with current season stats, weighting current season more heavily
as it progresses.

## Recent Trends (rolling 5-game window)

All NST individual and on-ice stats from the tables above, averaged over
the last 5 games. Captures recent form without overreacting to 1-2 game
blips.

- All individual per-60 stats over last 5 games
- TOI over last 5 games
- All on-ice stats over last 5 games (when available)

## Future Enhancements (later iterations)

Each of these needs individual evaluation for predictive power:

- **Opportunity score**: based on linemate/D-partner quality — how much
  a player's game is elevated by who they play with
- **Coach's trust factor**: penalty minute differential as a proxy
- **Teammate injuries**: can hinder a player (losing a key linemate) or
  increase opportunity (bigger role to fill)
- **Opponent-specific history**: players who historically perform well
  against certain teams

---

## Data Source Gaps

| Feature | Status | What's Needed |
|---------|--------|---------------|
| Opponent team stats (GAA, etc.) | **Not yet built** | NHL API team stats ingest |
| Opponent starting goalie | **Not yet built** | Daily Faceoff scraper (stub exists) |
| On-ice game logs | **Not yet scraped** | NST scraper supports it but doubles request volume |
| +/- per game | **Not available** | Derive from on-ice GF - GA, or find another source |
| PIM (historical) | Available in `SeasonStats` | Implemented in `SeasonAggregateExtractor` |

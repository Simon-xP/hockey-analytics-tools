# How I Made This

PuckAgent is a system that plays fantasy hockey.

It ingests every NHL play-by-play event and shift change, builds its own advanced stats and expected-goals model from that raw data, forecasts what each player will do in their next game, and then decides which adds, drops, and lineup moves maximise fantasy points in one specific 16-team Yahoo points league.

This page exists because nobody else can run it.

It is built for a single league, with a single user, against a Yahoo API whose terms make redistribution a non-starter.
The interesting part of the project is not a product anyone can click through, it is the sequence of decisions that got it here.
So this is the tour.

---

## The problem it solves

The league is a 16-team Yahoo points league with daily lineups.
Scoring is G=3, A=2, PIM=0.3, SOG=0.3, HIT=0.4, BLK=0.5.
Roster slots are 2C, 2LW, 2RW, 4D, 2G, 2UTIL, and you get four add transactions per week.

That combination makes the naive question ("who are the best available players?") the wrong one.
The actual question is closer to:

> Given the games left in this week, the slots I can actually fill on each of those days, the four adds I am allowed to spend, and the specific player I would have to drop to make room, which single swap on which single day produces the most points, and is it better than waiting until Thursday?

Every subsystem below exists to answer some piece of that sentence.

---

## Scale

Real row counts from the production database, not estimates.

| Table | Rows | What it is |
|---|---|---|
| `player_shifts` | 9,748,372 | Every shift by every player |
| `game_events` | 3,996,849 | Every play-by-play event |
| `game_advanced_stats` | 2,030,580 | Derived per-player per-game on-ice metrics |
| `shot_attempts` | 1,475,929 | Every shot attempt, each scored by the xG model |
| `shift_segments` | 1,223,256 | Maximal intervals with unchanged personnel, for RAPM |
| `games` | 12,812 | 2015-16 through 2025-26 |

Code, excluding generated files and dependencies:

| Layer | Python LOC |
|---|---|
| `src/ingest` | 4,906 |
| `src/optimize` | 4,887 |
| `src/analytics` | 3,037 |
| `src/predict` | 2,863 |
| `src/api` | 2,200 |
| `src/core` | 2,160 |
| `src/backtest` | 2,012 |
| `scripts` | 2,255 |
| `tests` | 2,934 |
| `frontend/src` | 8,140 (JSX + CSS) |

---

## How it fits together

Everything the system does falls into one of three questions: **get the data in**, **predict what happens next**, **decide what to do about it**.
The directory layout mirrors that, and each layer only depends on the layers below it.

```
FRONTEND  →  API  →  OPTIMIZE  →  PREDICT  →  ANALYTICS  →  INGEST  →  CORE
```

| Layer | Responsibility |
|---|---|
| `core` | Database models, the player resolver, league scoring weights, and temporally-gated read queries. Depends on nothing. |
| `ingest` | One subdirectory per source: NHL API, Natural Stat Trick, Yahoo Fantasy, Daily Faceoff, news, schedule. Resolves names to NHL IDs and writes rows. No analysis. |
| `analytics` | Derived metrics computed from raw events: shift-event correlation, expected goals, RAPM player ratings. Not predictions. |
| `predict` | What will a player do next. Situation-split per-60 models, a TOI model, and the upside and opportunity signals. Knows nothing about rosters or leagues. |
| `optimize` | What to do about it. Slot assignment, player valuation, drop ranking, goalie streaming, week optimisation, matchup win probability. The only layer that knows fantasy teams exist. |
| `backtest` | Replays historical decisions against the optimiser's strategies. Sits beside the stack, not inside it. |
| `api` | FastAPI, typed Pydantic response models, async handlers. |
| `frontend` | Vite + React, TanStack Query, ten pages. |

The rule that each layer only reaches downward is what makes the thing tractable.
The forecasting model has no idea a fantasy league exists.
The optimiser has no idea how a forecast is produced.
Either one can be rewritten without touching the other, which has already happened twice.

---

## Stack

| Choice | Used for | Why |
|---|---|---|
| PostgreSQL + SQLAlchemy 2.0 | Everything persistent | Ten million shift rows with real joins. A dataframe-and-parquet setup would have collapsed by month two. |
| Alembic | Schema migrations | Added late, in May 2026, once `init_db()` stopped being honest about the schema's actual state. |
| XGBoost | xG model, forecasting models, feature discovery | Tabular data with non-linear interactions and a lot of missingness. Gradient boosting is still the correct default. |
| FastAPI + Pydantic v2 | Backend | Typed response models mean the OpenAPI docs at `/docs` are generated, not written. |
| httpx | All outbound HTTP | Replaced `requests` for one reason: async, so the ingest layer can fan out. |
| React 19 + Vite | Frontend | Fast dev loop matters more than anything else when the UI is a scratchpad for exploring your own data. |
| TanStack Query v5 | Data fetching | Replaced a hand-rolled `useApi` hook. Caching, staleness, and refetching are not things worth re-implementing. |
| pytest, ruff, black | Quality | The leakage test suite is the only thing standing between this project and months of chasing invisible bugs. |

---

## The problems that were actually hard

These are the decision points worth reading about.
Each one changed the shape of the codebase.

### 1. Entity resolution had to come first

Different sources spell the same player differently: `Alex Ovechkin`, `A. Ovechkin`, `Ovechkin, Alex`.
Accents, punctuation, and word order all vary.
And some names are genuinely ambiguous: there are two Sebastian Ahos in the league, one a forward in Carolina, one a defenceman in St. Louis.

Nothing else can be built until joins across sources are trustworthy, so the first real module was `src/core/resolver/`.
It normalises names (strip accents, drop punctuation, sort words, so `"O'Reilly, Ryan"` and `"Ryan O'Reilly"` both become `oreilly ryan`), keeps a per-source alias table, and falls back through exact match, alias match, normalised match, fuzzy match, and finally fuzzy match plus team as a disambiguator.

NHL's official player IDs are the canonical key throughout.
They are stable and authoritative, and inventing a parallel ID space would have been a mistake with a long tail.

### 2. Deleting the data source and building it from scratch

The original pipeline scraped Natural Stat Trick for every advanced stat.
It worked, and it was a dead end: roughly 20 to 35 seconds per player, a daily request budget, and on-ice features already disabled because scraping both individual and on-ice game logs doubled the cost.
Full coverage of ~900 active skaters was never going to happen, and the whole project was one IP ban away from having no data at all.

The replacement was to derive everything from the NHL's own play-by-play and shift-chart endpoints.
That meant building the analytics layer that NST was providing: correlating ~600 shifts per game against ~250 events per game to work out who was on the ice for each event, decoding the 4-character `situationCode` field to split every metric by strength state, and computing Corsi, Fenwick, high-danger chances, zone starts, IPP, and TOI by situation.

It was the single largest piece of work in the project and it removed the project's only external dependency for derived stats.

### 3. An xG model that does not know who is shooting

The expected-goals model reached **0.833 AUC** on a 2025-26 holdout and is well calibrated (predicted 0.053 versus actual 0.053).
Published models land around 0.78 to 0.80 on the same public data, so this is competitive.

It is also structurally blind in a way that matters.
Like MoneyPuck and Evolving Hockey, it predicts goal probability from shot context: distance, angle, shot type, game state, rebound and rush detection, time since the last event.
It has no idea who took the shot.
A McDavid wrist shot from the slot gets exactly the same xG as a fourth-liner's wrist shot from the same coordinates.

This breaks the most tempting fantasy heuristic there is.
"His goals are way above his xG, so he is due to regress" is only true if the shooter has league-average finishing talent.
For a genuine sniper, beating xG for three straight seasons is not luck, it is the model missing a real skill.
The same error runs in reverse: a poor finisher underperforming their xG is not a buy-low, they are correctly priced.

The resolution was to treat context-only xG as a measure of *chance quality* and never as a luck detector on its own, then cross-reference against career shooting talent before labelling anyone a sell-high.
A shooter-adjusted xG variant is still open work, and the trade-off is real: adding shooter identity makes the model overfit badly on callups and low-TOI players.

### 4. The forecast was systematically too optimistic about stars

MacKinnon, Kucherov, Pastrnak, Kaprizov, and McDavid were all being projected two to three fantasy points **above** their season averages for a single game.
That is not a rounding error, it is the model claiming every elite player is having a career week, every week.
Downstream, it inflated add recommendations for anyone on a hot streak.

The cause was feature construction, not the model.
Rolling features used EWMA half-lives of 5, 10, and 15 games, which means the last five games contributed to all three windows.
XGBoost found recent form three times over and compounded it.

The fix was disjoint windows: L5 (games 0-4), L6-15, L16-30, so each game contributes to exactly one feature.
Season average stayed as the long-term anchor.
The bias disappeared, and feature importance shifted onto stable signals (`is_forward`, regressed IPP, blended season rates) instead of recent-form features.

| Player | Before | After |
|---|---|---|
| McDavid | +2.76 | +1.23 |
| MacKinnon | +1.94 | -0.84 |
| Kucherov | +1.58 | -1.14 |
| Pastrnak | +1.22 | -0.31 |
| Kaprizov | +1.40 | -0.32 |

Most stars now project slightly *below* their season average, which is correct: a season average includes their peak games, and a single-game projection should regress toward true talent.

### 5. Goalies are binary, and pretending otherwise was philosophically wrong

The first goalie valuation multiplied projected fantasy points by crease share, so a goalie starting 67% of games got 67% of his output in every game.

That number describes nothing that can happen.
A goalie either starts or he does not.
Averaging across the two produces a projection that is wrong in both branches, and it quietly made every goalie look like a mediocre skater.

It was replaced with a deterministic per-game prediction: a starter (crease share ≥ 60%) starts everything except the second night of a back-to-back, and committee or backup goalies are assumed not to start without confirmation.
Binary, occasionally wrong, never incoherent.

There was also no goalie game-stats table, so goalie stats are derived from `shot_attempts` instead: every shot carries a `goalie_id`, which gives saves, goals against, shots faced, shutouts, and wins by joining to game scores.

### 6. Adds are slot-aware, drops are not, and that asymmetry is deliberate

When evaluating a free agent, the system checks whether they would actually reach the active lineup on each game day, via bipartite matching against open slots, and only counts the games where they fit.
A great player who cannot get into the lineup is worth nothing this week.

The obvious move is to apply the same logic to drop candidates.
That is a bug.
The first version of the drop ranker showed Matthew Tkachuk at 0 weekly fantasy points because he happened to be slot-blocked on every game day that week, which made him look like the ideal player to cut.

Dropping a player frees their slot, so the value at risk is their full production, not their slot-constrained production.
Drop ranking therefore uses simple historical FPTS/GP times all games.
Two different questions, two different valuation paths.

### 7. Time leakage is the silent killer

Any backtest that can see the future is worse than no backtest, because it produces confident numbers instead of an error.
In a system where projections come from rolling windows over a games table, a single forgotten `WHERE` clause is enough to invalidate every result the project will ever produce.

The defence is architectural rather than careful.
Every function that reads player or game data takes an explicit `as_of: date`, defaulting to today for live use.
Queries filter on `Game.date < as_of`, strict less-than, so a decision made on day D cannot see day D's games.
`StatsProvider` and `ScheduleProvider` deliberately live in `src/core/queries/` rather than inside the backtest package, because live code and backtest code sharing one query path is the only reliable guarantee that they enforce the same boundary.
A dedicated leakage test suite asserts the boundary across every provider.

One known leak survives and is documented rather than hidden: the goalie functions in `src/optimize/goalies.py` filter by game ID range but not by date, so they see a full season regardless of `as_of`.

### 8. Upside and opportunity are two models, not one

There are two very different reasons to want a player who is not currently producing.

**Upside** is about the player: their finishing talent is being suppressed by bad luck, their underlying rates are trending up, they are young.
That signal persists for weeks and across seasons, because talent does not evaporate when the situation changes.

**Opportunity** is about the situation: a teammate got hurt, they moved up a line, they are on the first power-play unit now, they have four games this week.
That signal is temporary by construction and is evaluated over three to five games, not months.

Merging them into one "is this player undervalued" score was tempting and wrong.
Injury-driven deployment changes strongly predict the next three games and barely predict next season, so evaluated against a mixed set of targets they look like noise and get down-weighted, taking the genuine short-term signal with them.
Worse, the optimiser needs to know *which* kind of undervalued a player is, because it determines the action: stream him this week, or stash him for a month.

They are kept as separate models with separate features, separate time horizons, and separate evaluation targets.

### 9. Weekly snapshots are not how the game is played

The first backtest locked every decision in on Monday morning: build the free-agent pool once, greedily pick up to four swaps, score them against the rest of the week.
Fast, convenient, and not a simulation of anything.

Nobody plays that way.
A real manager looks every morning, sees who exploded last night, sees who got scratched, sees tonight's confirmed goalies, and makes one decision at a time.
Add slots get deliberately held back when Tuesday looks dry and Thursday looks good.
The value of the one-to-seven day window the optimiser reasons about shrinks as the week burns down: on Sunday morning that window is one day, not seven.

Monday snapshots also hide leakage (Friday data can quietly inform a Tuesday pick and nothing complains) and produce transactions with no day attached, which makes them impossible to compare against real Yahoo timestamps.

The refactor to a day-by-day loop, where `decide_today()` can legitimately return "do nothing" as a first-class answer, is planned across seven phases with the leakage plumbing first.

### 10. A 50-point deficit on Monday is not the same as a 50-point deficit on Saturday

Every transaction decision is weighted by an aggression level (CONSERVATIVE, NORMAL, AGGRESSIVE, DESPERATE, PREPARE), which controls the trade-off between player quality and schedule volume, the threshold for firing a swap, and how harshly injuries are penalised.

For a long time it was hardcoded to NORMAL, and the first attempt to compute it was a margin-based decision tree that ignored the one thing that matters most: how much hockey is left.

The replacement models both teams' remaining points as normal distributions, sums per-game variance across remaining fillable games using a coefficient of variation of about 0.45 for hockey fantasy points, and folds in a distribution for the pickups each side can still make.
Aggression comes from the resulting win probability.
The same 50-point gap yields NORMAL on Monday, when variance is high and anything can happen, and DESPERATE on Saturday, when it cannot.

`PREPARE` is the interesting state: when a matchup is either already won or already lost, the horizon shifts entirely to next week and current-week value is discarded.
Whether `PREPARE` can trigger at all depends on how much the week matters, which is where the design starts needing a standings simulation.

### 11. Two things it still cannot do

**Rookies.** Players with no meaningful history project to zero.
This league does not draft rookies, so the workaround has held, but valuing a prospect properly needs draft pedigree, AHL production, and comparable-player matching, none of which is ingested.

**Linemate quality.** The single biggest missing input.
Opportunity scoring wants to know "did this player get promoted to play with better teammates", and answering that requires a real player rating system.
That is what the RAPM work is: ridge-regularised adjusted plus-minus over 1.2 million shift segments, with one-sided encoding so that offensive and defensive contributions do not contaminate each other, and WOWY analysis on top to measure how much a player elevates the teammates around him.

---

## Timeline

| Date | Milestone |
|---|---|
| Oct 2025 | First commit. A Natural Stat Trick scraper writing nine situational stat splits into Postgres. |
| Dec 2025 | Restructured into a layered architecture. Player resolver, NHL API client, 32 teams and 817 players seeded. |
| Feb 2026 | Schedule optimiser: roster and league settings schema, bipartite-matching slot availability. |
| Mar 2026 | Forecasting module scaffolded. Game scores and opponents backfilled so rolling features become computable. |
| Apr 2026 | The big month. XGBoost forecasting, FastAPI backend, React frontend, Yahoo OAuth integration. Then the NHL API event pipeline, the xG model, the advanced stats engine, and forecasting v2, which together retired the NST dependency. Then the transaction evaluator. |
| May 2026 | Infrastructure hardening: Alembic migrations, Pydantic response models with async handlers, httpx, TanStack Query. |
| In flight | Layered split of the old `src/tools/` into `analytics` / `predict` / `optimize` / `backtest`. RAPM player ratings (851 rated players so far), team roster tracking, and player valuation persistence. |

---

## Build log

The entries below are the working journal, kept as they were written at the time rather than tidied up afterwards.

<!--
NOTE TO SELF: entries 1-10 are carried over from docs/journal.md, which stopped
in April 2026. The gap between then and now is stubbed out below with the
factual record from git so it's ready to write into. Keep the voice yours.
-->

### Entry 1: Project assessment and planning

**2025-12-01**

Starting point was a loose collection of files: an NST scraper writing to Postgres, a Jupyter notebook for exploration, and a schedule CSV with AI-generated function stubs that were not connected to any database.

The problem identified: multiple data sources would all need to reference players, but different sources use different name formats, some names are ambiguous (two Sebastian Ahos), and there was no central player database to join on.

Decision: entity resolution first.
Before building anything else, there needs to be a player resolution layer that maintains a canonical player table on NHL's official IDs, stores known aliases per source, provides fuzzy matching for unknown formats, and uses team and position to break ties.
Everything else builds on that.

### Entry 2: Architecture design

**2025-12-01**

Two options considered: a flat `hockey/` package with everything in it, or a domain-separated structure with clear layer boundaries.

Went with domain-separated, because there were going to be many data sources (NHL API, NST, Daily Faceoff, and more), each with different update patterns, and modifying one source should not require touching another.
Clear separation makes the dependency graph legible.

Created `src/core/` (models, resolver, db), `src/ingest/` (one subdirectory per source), and `src/tools/` (user-facing functionality), plus `config/`, `data/`, `docs/`, `scripts/`, `tests/`, and `notebooks/`.

### Entry 3: Core layer implementation

**2025-12-16**

Models created: `Team`, `Player`, `PlayerAlias`, `Game`, `GoalieStart`.
Key decisions were to use NHL's official IDs as primary keys because they are stable and authoritative, to store a `normalized_name` for fast lookups, and to track `source` on every alias so it is always clear where a name variation came from.

The resolver went in as three files: `normalize.py`, `matcher.py`, and an `__init__.py` holding the `resolve_player()` entry point.
Normalisation strips punctuation and accents and sorts words, so `"O'Reilly, Ryan"` and `"Höglander"` become `oreilly ryan` and `hoglander`.
Matching priority runs NHL ID, then known alias, then normalised name, then fuzzy via rapidfuzz, then fuzzy plus team.

### Entry 4: Migration of existing code

**2025-12-16**

Moved the NST scraper and its config into `src/ingest/natural_stat_trick/`, the schedule API into `src/tools/schedule/optimizer.py`, the raw schedule CSV into `data/raw/`, and the notebooks into `notebooks/`.
Fixed config paths to use `pathlib` relative to the script location and switched the shell script to `python -m` module syntax.

### Entry 5: Package configuration

**2025-12-16**

`pyproject.toml` with dependencies (pandas, sqlalchemy, psycopg2-binary, rapidfuzz, unidecode, requests, beautifulsoup4) and optional dev extras (pytest, black, ruff).

### Entry 6: NHL API client and database seeding

**2025-12-17**

Created the `hockey` database, initialised tables, set up the virtual environment.
Built `src/ingest/nhl_api/` with a `client.py` and a `seed.py`, hitting `standings/now` for teams, `roster/{team}/current` for rosters, and the stats REST API for team IDs.
Added a 0.5s delay between requests to avoid 429s.

Seeded 32 teams and 817 players.
Resolver verified against real data: `"Nathan MacKinnon"` and `"Nate MacKinnon"` both resolve to 8477492, and `"Sebastian Aho" + CAR` correctly picks 8478427.

Settled on a snowflake schema: `players`, `teams`, and `games` as dimensions, `goalie_starts` and future game logs as facts, with `players` as the central dimension everything joins to.

### Entry 7: Natural Stat Trick integration

**2025-12-18**

Refactored the scraper into clear sections and gave it `--historical` (2019-2024) and `--current` modes, with a 10-second delay between requests and a mapping for NST's team abbreviations (`S.J` to `SJS`, `L.A` to `LAK`).

Several resolver improvements fell out of running against real data.
`roster/{team}/current` only returns the active roster and misses players on IR, so `roster/{team}/{season}` was added for the full season roster, plus a `get_skaters_with_games()` pass to catch AHL callups who played NHL games.
Lowered the fuzzy threshold from 85% to 80%, which catches Zack versus Zachary and Max versus Maxwell.
Added a team fallback that tries fuzzy matching with a team filter first and then without, to handle players who were traded mid-season.

Results: 1,092 players, 17,422 season stat records across six seasons, 99.9% resolution rate.
The single unresolved player was Pierre-Olivier Joseph, listed as "Pierre-Olivier" by NST and "P.O" in the database.

### Entry 8: Fantasy schedule optimiser

**2025-12-18**

New goal: a schedule optimiser for a daily-lineup Yahoo points league.
Two components, schedule and roster flexibility, then fantasy point predictions.

The core problem is that maximising games played is constrained by position slots, multi-position eligibility, and team schedules all at once.
Multi-position eligibility is what makes it genuinely hard: a player eligible at C and LW affects both pools, which makes this an assignment problem rather than a sorting problem.
Plan is bipartite matching to assign players to slots optimally, then read off the remaining capacity.

Decided to build the schedule half first, because the prediction half needs more research into which factors actually matter.

### Entry 9: Transaction evaluator (Phase 1 of PuckAgent)

**2026-04-12**

Built the decision engine that answers "should I add this player?", treating the forecast model as a black box input and focusing on the infrastructure around it: slot-aware valuation, drop ranking, goalie streaming, weekly optimisation under a four-add constraint.

Key design decisions:

**Forecast as black box.** The evaluator wraps the v2 model behind `_default_forecast_fn(nhl_id, game_date, avg_toi) -> float`.
Behind that signature it extracts features, predicts per-60 rates per situation, predicts TOI per situation, and combines them into per-game fantasy points, but the evaluator knows none of it.
This keeps forecast iteration and evaluator iteration on independent timelines.

**Slot-aware for adds, simple for drops.** Free agents only get credit for games where they would actually make the active lineup.
Rostered players being considered for a drop get valued at full historical FPTS/GP across all games, because dropping frees the slot.
Using slot-aware logic for drops makes any bench-blocked player look like zero value, and you end up always dropping them.

**Goalies are deterministic, not probabilistic.** An earlier version multiplied goalie fantasy points by crease share, so a 67% starter got 67% of his output every game.
That is philosophically wrong, goalies either start or they do not.
Replaced with a binary per-game `predict_starts()`.

**Goalie stats from `shot_attempts`.** No dedicated goalie game-stats table exists, but every shot has a `goalie_id`, which is enough for saves, goals against, shots against, wins, and shutouts.

**Live Yahoo, no static config.** Dropped `config/roster.json` in favour of `load_roster_from_yahoo()`.

Iterations that only surfaced by running against the real roster:

1. Goalies getting absurd numbers out of the skater forecast, fixed with an early return on player type.
2. Drop ranking showing M. Tkachuk at 0 weekly FPTS because he was slot-blocked all week, fixed by making the drop ranker non-slot-aware.
3. Drop ranking reading 2024-25 data because `compute_fpts_per_gp` only read NST, fixed by preferring `GameAdvancedStats` with NST as fallback.
4. The v2 forecast producing huge numbers for retired players (Suter 37, Vlasic 33, Pacioretty 31), fixed upstream in the model.
5. The probabilistic goalie discount, replaced as described above.

### Entry 10: Forecasting v2, non-overlapping windows and a 5-season retrain

**2026-04-15**

Problem: top-tier skaters were being projected 2 to 3 fantasy points above their season averages for single-game forecasts, which inflated add/drop recommendations for hot streakers and flagged elites as must-haves even when they were cold.

Root cause: rolling features used overlapping EWMA half-lives of 5, 10, and 15 games, so the last five games contributed to all three windows.
XGBoost latched onto recent form and compounded it across features.

Changes:

1. **Non-overlapping rolling windows.** Replaced the EWMA half-lives with disjoint `L5` (games 0-4), `L6_15` (5-14), and `L16_30` (15-29). Each game contributes to exactly one window. Season average stays as the long-term anchor, and the prior-season and blended features handle cold start.
2. **5v5 empirical Bayes blend for goals and assists.** Extended `blend_xgb_with_eb` with an `only_stats` parameter so 5v5 goals and assists get credibility-weighted toward a prior while hits, blocks, and shots stay pure XGBoost. Cuts variance for players with sparse 5v5 scoring samples without touching the high-volume stats.
3. **5-season retrain.** Ingested 2021-22, the only missing post-COVID season, and retrained 5v5 and PP across five seasons. 5v5 training samples went from 38k to 221k, PP from 20k to 128k.

The upward bias on elites is gone, and most stars now sit slightly below season average, which is the expected direction.
No calibration pass was needed.
Feature importance on the 5-season model leans much harder on stable signals: `is_forward` at 0.457, then regressed IPP and the blended goal and shot rates.
Recent-window features still appear but no longer dominate.

Verified no leakage: `load_player_game_stats` uses a strict `g.date < :before_date` filter, walk-forward training extracts features at each historical date using only prior games, and season aggregates are computed game by game rather than from end-of-season totals.

### Entry 11: Upside and opportunity split apart

**May 2026** &middot; *To write*

> **On the record:** `src/predict/signals/` now holds `upside.py` and `opportunity.py` as two independent models, each with hand-tuned component scores awaiting empirical weights.
> The design reasoning is in `docs/upside-and-opportunity.md`, including the stability-selection and reciprocal-rank-fusion methodology for feature discovery.

### Entry 12: Infrastructure hardening

**2026-05-19** &middot; *To write*

> **On the record:** four commits in one day. Alembic with a baseline schema replacing `init_db()`, Pydantic response models plus async route handlers across the API, `requests` swapped for `httpx` across the whole ingest layer, and the hand-rolled `useApi` hook replaced with TanStack Query across all frontend pages.
>
> **Worth recording:** why all four landed together, and what specifically made `init_db()` stop being viable.

### Entry 13: The layered restructure

**In flight** &middot; *To write*

> **On the record:** `src/tools/` was split into `src/analytics/`, `src/predict/`, `src/optimize/`, and `src/backtest/`, on the principle that the three real stages are get the data in, predict what happens next, and decide what to do about it.
>
> **Worth recording:** what forced this, given the December 2025 structure had held for five months.

### Entry 14: RAPM and player ratings

**In flight** &middot; *To write*

> **On the record:** `shift_segments` is populated at 1,223,256 rows and `player_ratings` at 851, so the offensive model has run.
> Design is in `docs/rapm-design.md`: ridge-regularised adjusted plus-minus over 5v5 segments, one-sided encoding so offence and defence do not contaminate each other, xG from the trained model as the response variable, and WOWY elevation on top.
>
> **Worth recording:** how the ratings actually looked against intuition on first run.

### Entry 15: Daily decision loop

**Planned** &middot; *To write*

> **On the record:** design is in `docs/daily-backtest-refactor.md`. Seven phases, leakage plumbing first.

---

## Lessons so far

1. **Entity resolution is foundational.** Get it right first and everything downstream becomes easier.
2. **Separate by domain, not by file type.** Keep the scraper, its config, and its docs together.
3. **Use canonical IDs from authoritative sources.** NHL player IDs are stable. Do not invent your own.
4. **Normalise early, match consistently.** Every name comparison goes through the same function.
5. **Test infrastructure against real data early.** Goalies in the skater pipeline, slot-aware drops, and the probabilistic goalie discount were all invisible until the code ran against an actual roster.
6. **Keep the forecast and the decision engine independent.** Each has been rewritten without touching the other.
7. **Owning the raw data beats consuming derived data.** Replacing the scraper with a play-by-play pipeline was the largest single piece of work and it unlocked everything after it.
8. **A projection that is never exactly right beats one that is never coherent.** The binary goalie prediction is wrong more often than the crease-share average and is far more useful.
9. **Make time boundaries structural, not careful.** An explicit `as_of` on every read, and one shared query path between live and backtest code, is the only real defence against leakage.

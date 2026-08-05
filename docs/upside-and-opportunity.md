# Upside vs Opportunity: Two Distinct Player Signals

PuckAgent's transaction system uses two separate signals to evaluate players
beyond their baseline forecast. These are deliberately kept as independent
models because they predict different things, use different features, operate
on different time horizons, and would degrade each other if combined.

## Upside — "How good can this player be based on their own talent?"

Upside measures a player's individual ceiling relative to their current
production. It answers: is this player better than their recent stats suggest?

**What it captures:**
- Shooting talent suppressed by bad luck (goals << ixG)
- Underlying rate improvements (ixG/60, scoring chance generation trending up)
- Career development trajectory (age, historical comparable players)
- Skill indicators that haven't translated to points yet

**What it does NOT capture:**
- Deployment changes from external events (teammate injuries, trades)
- Line combination changes
- Coach decisions about ice time allocation
- PP/PK unit reshuffling

**Time horizon:** Medium to long. Upside signals should persist across weeks
and into the next season. A player with genuine upside is underperforming
their talent — that talent doesn't evaporate when the situation changes.

**Evaluation targets for feature discovery:**
1. FPTS/GP delta, next 10 games vs trailing average
2. FPTS/GP delta, next 20 games vs trailing average
3. ixG/60 delta, next 10 games (luck-free quality measure)
4. Next season FPTS/GP vs current season FPTS/GP (true breakout)
5. Next season TOI/GP vs current season TOI/GP (structural role earned)

**Candidate features for discovery:**
- Shooting luck: goals vs ixG (current — regression signal)
- Process vs results: on-ice xGF% vs actual points (current)
- ixG/60: individual expected goals generation rate
- ixG per shot attempt: shot quality independent of volume
- HDCF/60: high-danger chance generation rate
- Shooting % vs career average: personal regression signal beyond xG
- Primary assist ratio: A1 / (A1 + A2) — repeatable playmaking skill
- PP production rate: points/60 when given PP time — latent scoring talent
- On-ice xG differential relative to teammates: play-driving talent
- Age: younger players have more breakout potential
- Prior season production: underperforming an established baseline
- Career shooting talent: EB-shrunk goals-over-expected per shot

**Current implementation:** `src/predict/signals/upside.py` — two
hand-tuned components (shooting luck, process vs results). To be replaced
by empirically-weighted features once the feature discovery harness
identifies which signals actually predict breakouts.

## Opportunity — "How good is the situation this player is in currently?"

Opportunity measures how favorable a player's current deployment and context
are, independent of their individual talent level. It answers: is this player
in a position to produce right now?

**What it captures:**
- Teammate injury creating a role expansion (more TOI, PP time)
- Line combination changes (promoted to play with better linemates)
- PP/PK unit changes
- Coach deployment decisions (zone starts, matchup usage)
- Schedule density (upcoming games this week)

**What it does NOT capture:**
- Whether the player has the talent to capitalize on the opportunity
- Long-term skill development
- Career trajectory

**Time horizon:** Short. Opportunity signals are inherently temporary. An
injury-driven promotion ends when the teammate returns. A favorable schedule
window closes after the week. Opportunity is evaluated on days-to-weeks, not
months.

**Evaluation targets for feature discovery:**
1. FPTS/GP delta, next 3 games
2. FPTS/GP delta, next 5 games

Cross-season targets are irrelevant for opportunity — the whole point is that
these are transient situations.

**Candidate features for discovery:**
- Recent TOI (last 5 games average): direct measure of current deployment
- PP deployment: PP TOI per game, PP unit (1 vs 2 if detectable from shift overlaps)
- Linemate quality: requires a player rating system (1-100) — biggest infrastructure gap
- Zone starts: offensive vs defensive faceoff ratio (OZS%)
- Team offensive strength: team GF/game or team xGF/60
- Deployment share trend: player's % of team TOI (5v5 and PP separately)
- Even strength vs special teams split: what % of TOI is PP?
- Schedule density: team games in upcoming window
- Injury-driven TOI bump: when a teammate's injury is detected, project
  increased TOI for beneficiaries before it shows up in actual game data

**Current implementation:** `src/predict/signals/opportunity.py` — two
hand-tuned components (TOI trend, deployment share). Separated from upside
in May 2026.

**Key infrastructure gaps:**
- Linemate quality / player rating system (not yet built)
- Injury pipeline integration (`src/ingest/news/injuries.py` exists but
  not wired into opportunity scoring)
- Daily Faceoff line combination scraping (`src/ingest/daily_faceoff/` —
  stub only). Provides causal signals for why deployment changed.

## How They Combine in Transaction Decisions

Both signals feed into the transaction evaluator as separate overlays on top
of the baseline forecast:

```
transaction_score = baseline_value + upside_weight * upside_score + opportunity_weight * opportunity_score
```

The weights differ by decision context:
- **Waiver pickup (streaming):** opportunity matters more — you want production
  this week
- **Speculative add (stash):** upside matters more — you're betting on the
  player's talent emerging
- **Hold/drop patience:** upside extends patience (talent is real, wait for it);
  opportunity without upside means drop when the situation reverts

## Feature Discovery Methodology

Both models will have their hand-tuned weights replaced by empirically
learned weights via a shared methodology. The goal is to answer "what
signals actually predict future outperformance?" rather than guessing.

### Approach

1. **Define evaluation targets** — multiple FPTS-based definitions of
   "did this player get better?" so we're not overfitting to one metric.

   Upside targets (medium-to-long horizon):
   - FPTS/GP delta, next 10 games vs trailing average
   - FPTS/GP delta, next 20 games vs trailing average
   - ixG/60 delta, next 10 games (luck-free underlying quality)
   - Next season FPTS/GP vs current season FPTS/GP (true breakout)
   - Next season TOI/GP vs current season TOI/GP (structural role earned)

   Opportunity targets (short horizon):
   - FPTS/GP delta, next 3 games
   - FPTS/GP delta, next 5 games

2. **Build wide candidate feature matrices** — compute all candidate
   features at every player-date evaluation point. Use trailing 20-game
   FPTS/GP as the baseline (simple, avoids coupling to forecasting model).

3. **Stability selection** — for each target, subsample data 50 times
   (50% of data), fit XGBoost, record which features land in top-K.
   Features appearing 60%+ of the time are "stable" for that target.
   Ref: Meinshausen & Bühlmann (2010).

4. **Multi-season backtesting** — run across 4+ seasons (2021-22 through
   2025-26) using `game_advanced_stats`. More data makes stability
   selection more trustworthy.

5. **Reciprocal Rank Fusion** — take feature importance rankings from each
   target and fuse them. Features that rank highly across most definitions
   of "improvement" are the real signals. Features that only look good
   under one definition are noise. Score = sum of 1/(k + rank) across
   all target rankings.

6. **Horizon tagging** — run the pipeline at multiple horizons (3, 10, 20,
   next-season). Temporary signals show up at short horizons and fade.
   Structural signals persist. Tag each feature with its stable horizon
   range for use in context-dependent decisions.

### Output

A consensus feature ranking per model (upside and opportunity separately),
with each feature tagged by horizon range. These rankings directly inform
which features to include and what weights to assign — replacing the
current hand-tuned component scores.

## Why They Must Stay Separate

Combining upside and opportunity into one model would:
1. **Muddy feature importance** — injury-driven deployment changes would show
   as predictive of short-term FPTS but not next-season breakout, making them
   look noisy when evaluated against all targets
2. **Conflate time horizons** — short-term opportunity features would dilute
   long-term talent signals and vice versa
3. **Make the transaction logic opaque** — the optimizer needs to know WHY a
   player scores highly to make the right decision (stream vs stash vs hold)
4. **Reduce backtest quality** — evaluating temporary signals against
   permanent targets (and vice versa) adds noise to the feature discovery
   process

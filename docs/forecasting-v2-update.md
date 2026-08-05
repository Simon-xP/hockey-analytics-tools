# Forecasting v2 — April 2026 Update

Short handoff note for the transaction-automation LLM.

## What changed

1. **Non-overlapping rolling windows.** Features now use disjoint game
   windows `L5` / `L6_15` / `L16_30` + season average, instead of
   overlapping EWMA half-lives. Each game contributes to exactly one
   window, so recent form is no longer triple-counted.

2. **5v5 Empirical Bayes blend (goals/assists only).** 5v5 scoring
   rates are credibility-weighted toward a prior for low-sample
   players. Hits/blocks/shots are unaffected.

3. **Trained on 5 seasons.** 2021-22 through 2025-26 (skipping the
   COVID-shortened 2020-21). ~221k 5v5 samples, ~128k PP samples.

## Impact on projections

Elite players no longer carry a systematic upward bias. A star on a hot
streak will still project above average, but by a reasonable margin.
Stars on a cold streak will project slightly below season average —
this is expected regression, not a bug.

Rule of thumb for interpretation:
- `proj - season_avg > +1.0`: player is meaningfully hot, buy signal
- `proj - season_avg < -1.0`: player is meaningfully cold, sell/bench signal
- Within ±0.5: model agrees with season form, no strong signal

## How to call

Public API is unchanged **except** you must now pass an `eb_5v5`
predictor to `forecast_player`:

```python
from src.predict.forecasting.forecast import load_models, forecast_player
from src.predict.forecasting.toi_model import TOIPredictor
from src.predict.forecasting.empirical_bayes import EmpiricalBayesPredictor

models = load_models()
toi = TOIPredictor()
eb_pp  = EmpiricalBayesPredictor("pp",  ["goals", "assists", "shots"])
eb_pk  = EmpiricalBayesPredictor("pk",  ["goals", "assists"])
eb_5v5 = EmpiricalBayesPredictor("5v5", ["goals", "assists"])  # NEW

proj = forecast_player(
    session, nhl_id, game_date,
    models=models, toi_predictor=toi,
    eb_pp=eb_pp, eb_pk=eb_pk, eb_5v5=eb_5v5,
)
# proj["fpts"]                       — single-game fantasy point projection
# proj["predicted_rates"][situation] — per-60 rates per situation
# proj["predicted_toi"][situation]   — predicted TOI in seconds per situation
```

The transaction evaluator's `_default_forecast_fn` already wraps this —
no changes needed in `src/optimize/`.

## Data leakage

None. `load_player_game_stats` filters with `g.date < :before_date`, so
every feature at prediction time `T` is computed only from games played
strictly before `T`. Training uses walk-forward extraction with the
same filter, so the model never sees its own target.

## Model artifacts

- `models/forecasting_v2/5v5_model.pkl`
- `models/forecasting_v2/pp_model.pkl`

Retrain command:
```bash
PYTHONPATH=. hockey-venv/bin/python -m scripts.train_forecasting_v2 \
  --train 20212022 20222023 20232024 20242025 20252026 \
  --situations 5v5 pp --no-eval
```

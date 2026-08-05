"""Prediction layer — what will a player do next.

- `forecasting/` — situation-split (5v5/PP/PK/other) per-60 rate and TOI
  models that combine into per-game fantasy point projections
- `signals/`     — upside and opportunity scores, the adjustments layered on
  top of a raw forecast

Reads derived stats from `src.analytics` and raw data from `src.core`.
Knows nothing about rosters, leagues, or transactions.
"""

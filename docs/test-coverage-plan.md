# Test Coverage Plan

Instructions for adding unit tests to PuckAgent's core modules. The codebase has 56 tests
currently, mostly integration-level. This plan focuses on unit tests that don't require
a database connection.

## Conventions

- Tests live in `tests/`, mirroring `src/` structure
- Use pytest classes (e.g. `class TestFeatureAllowedForStat:`)
- No database access for unit tests — mock or use fixtures
- Run with `pytest tests/` or `pytest tests/test_file.py::TestClass::test_name`
- Existing test style: see `tests/test_forecasting.py` for reference

## Priority 1: Forecasting v2 feature filtering

File: `tests/test_forecasting_v2.py`

Test `feature_allowed_for_stat()` from `src/predict/forecasting/constants.py`.
This is the gate controlling which features each stat model sees. A bug here
silently breaks model quality with no error.

What to test:
- Each stat (goals, assists, shots, hits, blocks) allows its own rolling/prior/blended features
- Each stat excludes unrelated stats (e.g. goals model excludes hits/blocks/penalties)
- Universal features (opp_*, is_home, is_b2b, days_rest, is_forward, is_center, season_gp, prior_gp, toi) are allowed for ALL stats
- hits features are allowed for assists and shots (cross-stat correlation) but NOT goals or blocks
- IPP features (ipp_*) only allowed for goals and assists
- sh_pct only allowed for goals
- Edge cases: feature names that are substrings of allowed patterns (e.g. "penalties_drawn" should NOT match "penalties" for hits since we removed penalties from hits)

Key fixtures — use the actual feature list from a trained model:
```python
import pickle
with open("models/forecasting_v2/5v5_model.pkl", "rb") as f:
    data = pickle.load(f)
# data["feature_columns"] is a dict: stat -> list of feature names
```

Or just test with known feature names directly (no pickle needed).

## Priority 2: Forecasting v2 model — per-stat feature columns

File: `tests/test_forecasting_v2.py` (same file)

Test `SituationModel._get_feature_columns()` from `src/predict/forecasting/model.py`.

What to test:
- When `feature_columns` is a dict (new format): returns the list for the requested stat
- When `feature_columns` is a list (old format, backwards compat): returns the full list regardless of stat
- Returns empty list for unknown stat when using dict format

Test `SituationModel._feature_vector()`:
- Produces correct-length vector matching the stat's feature columns
- Missing features become NaN
- Non-finite values become NaN

## Priority 3: Forecasting v2 feature extractors

File: `tests/test_forecasting_v2_features.py`

These are pure functions that take game data and return feature dicts. They can be
tested with synthetic game data (no DB needed).

### extract_rolling_features (features.py)

Input: list of game dicts (most-recent-first), each with keys like:
```python
{"goals": 1, "shots": 5, "hits": 3, "blocks": 2, "toi_seconds": 900,
 "first_assists": 0, "second_assists": 1, "ixg": 0.3, "shot_attempts": 8,
 "penalties": 1, "penalties_drawn": 0, "cf": 20, "ca": 15, "xgf": 1.5,
 "xga": 1.0, "hdcf": 5, "ff": 18, "fa": 13, "sf": 10, "sa": 8,
 "gf": 2, "ga": 1, "scf": 8, "sca": 5, "hdca": 3,
 "oz_starts": 5, "dz_starts": 3, "nz_starts": 2, "ipp": 0.5,
 "faceoff_wins": 5, "faceoff_losses": 3}
```

What to test:
- season_gp equals len(games)
- Empty games list returns only {"season_gp": 0.0}
- L5 window uses games[0:5], L6_15 uses games[5:15]
- Per-60 rates: (stat / toi_seconds) * 3600
- season_avg is mean of all games
- NaN values in ratios when denominator is 0 (e.g. sh_pct when shots=0)

### extract_blended_features (features.py)

What to test:
- At 0 GP: returns 100% prior
- At k GP (k=20): returns 50/50 blend
- Missing prior returns current only
- Missing current returns prior only

### extract_ipp_features (features.py)

What to test:
- ipp_regressed uses position-specific mean and k=20 stabilization
- Forward vs defenseman get different position means
- Season raw IPP is just the unregressed value

## Priority 4: xG model feature matrix

File: `tests/test_xg.py`

Test `build_feature_matrix()` from `src/analytics/xg/model.py`.

What to test:
- Output shape is (n_shots, 30)  [30 = len(FEATURE_COLUMNS)]
- Shot type one-hot encoding: exactly one 1.0 per shot for known types, all 0.0 for unknown
- Last event type one-hot: same pattern
- Boolean features (is_home, is_rebound, is_rush) are 0.0 or 1.0
- NaN handling for missing values (e.g. first shot of game has no time_since_last_event)

## Priority 5: Transaction scoring

File: `tests/transactions/test_scoring.py`

Test `score_transaction()` from `src/optimize/`.

What to test:
- Positive marginal FPTS produces positive score
- Score scales with aggression level
- Drop candidate with more remaining games penalizes the transaction
- Zero marginal FPTS produces score near zero

## What NOT to test

- Database queries (those are integration tests, already covered by test_no_leakage.py)
- End-to-end model training (too slow, use backtests instead)
- Frontend/API (separate concern)
- Exact model predictions (they change with retraining)

## Running tests

```bash
pytest tests/                              # all tests
pytest tests/test_forecasting_v2.py -v     # verbose, single file
pytest tests/ -k "feature_allowed"         # by keyword
pytest tests/ --tb=short                   # shorter tracebacks
```

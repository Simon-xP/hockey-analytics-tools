"""Per-game and per-team fantasy-point variance.

A projection is only half the picture. Under a `DELTA P(win)` objective this
curve *is* the optimizer's risk behaviour: it decides whether a volatile
streamer beats a steady one when we are trailing, and the reverse when we are
ahead. So it is measured, not assumed.

What is measured is **predictive** variance — how far actual outcomes land
from what our own model projected — not the raw variance of game FPTS. That
folds true game-to-game noise together with our projection error, which is
exactly the uncertainty `P(win)` should reflect.

Measurement
-----------

`scripts/fit_variance_model.py`, run 2026-08-02 against season **20252026**:
10,036 skater-games across 40 evenly spread game dates, projected with the
live `forecast_player` path at `as_of = game_date` and compared to actuals
from `game_advanced_stats`.

Three forms were fitted to residual sd bucketed by projected FPTS (20
equal-count buckets), weighted by bucket size:

| form | fit | weighted RMSE |
|---|---|---|
| constant CV | `sigma = 0.660 * mu` | 0.164 |
| **affine** | **`sigma = 0.448 + 0.526 * mu`** | **0.104** |
| power | `sigma = 0.871 * mu ** 0.774` | 0.106 |

Affine wins, as `docs/plans/weekly-optimizer/02-substrate.md` predicted. The
measured ratio `sigma/mu` runs from 0.83 at the low end to 0.59 at the high
end, so a constant CV understates noise on low-projection players — precisely
the streaming pool the optimizer spends its adds on. The old `CV = 0.45`
understated it everywhere: the true figure is nearer 0.66, so every sigma in
the system was roughly a third too small.

Calibration
-----------

On synthetic 25-player-game weekly bundles, the model's 80% interval covers
**80.2%** of actual totals. That is the test that says a `P(win)` objective
is safe to build on this, and it passes.

It passes only after removing the projection's mean bias. As shipped, the
forecast over-projects by **0.49 FPTS per game** (3.08 projected against 2.59
actual, a ratio of 1.19), which drops raw interval coverage to 52%. The bias
is not in the TOI model (predicted TOI is within 3% of actual) but in the
per-60 rates, and it is broad: assists +27%, goals +19%, blocks +19%, hits
+13%, shots +12%. That belongs to `src/predict/` and is reported, not patched
here. Until it is fixed, treat absolute `P(win)` as optimistic; differences
between two of our own projections are far less affected, because the bias is
close to multiplicative and largely cancels in a gap.

Correlation
-----------

Linemates are not independent — one goal credits two or three of them — so
the independent sum was checked rather than assumed. Mean residual
correlation between same-team skaters on shared game dates is **r = +0.074**
over 333 player pairs, below the 0.15 threshold the plan set for adding a
pairwise correction. Variances continue to add.

Range of validity
-----------------

Fitted over projected FPTS from 1.05 to 9.45. Below roughly 1.7 the affine
form is an extrapolation: the sample gate (10 prior games in the season)
excludes call-ups, who are the only players who project that low. The
intercept keeps sigma positive there, which is the right shape, but the exact
value is not measured.

Goalies
-------

`game_sigma` dispatches on `player_type`. The goalie branch is a placeholder
that reuses the skater curve; P3 owns the fitted goalie coefficients and the
Bernoulli start-uncertainty term on top of them.
"""

from __future__ import annotations

from typing import Sequence

from src.optimize.models import PlayerType

# ---------------------------------------------------------------------------
# Fitted coefficients
# ---------------------------------------------------------------------------

# sigma = SKATER_SIGMA_INTERCEPT + SKATER_SIGMA_SLOPE * projected_fpts
# Season 20252026, 10,036 skater-games. See the module docstring.
SKATER_SIGMA_INTERCEPT = 0.4478
SKATER_SIGMA_SLOPE = 0.5262

# The constant-CV alternative, kept for reference and for anything that still
# wants a single ratio. Measured at 0.66, not the 0.45 this module used to
# assume. Do not build on it: it is the worst-fitting of the three forms.
MEASURED_CV = 0.6597

# Mean residual correlation between same-team skaters, season 20252026.
# Below 0.15, so `team_sigma` sums variances independently.
LINEMATE_CORRELATION = 0.074

# Owned by P3 (goalies). While this is None, `game_sigma` falls back to the
# skater curve for goalies. P3 replaces it with fitted `(intercept, slope)`;
# the Bernoulli start-uncertainty term lives in P3's `goalie_game_var`.
GOALIE_SIGMA_COEFFICIENTS: tuple[float, float] | None = None


def game_sigma(projected_fpts: float, player_type: PlayerType) -> float:
    """Standard deviation of one player-game's fantasy points.

    Args:
        projected_fpts: What we expect this player to score in this game.
        player_type: Skater or goalie. Goalies currently share the skater
            curve; see the module docstring.

    Returns:
        Predictive sigma in fantasy points — game-to-game noise and our own
        projection error together.
    """
    mu = max(0.0, float(projected_fpts))

    if player_type is PlayerType.GOALIE and GOALIE_SIGMA_COEFFICIENTS is not None:
        intercept, slope = GOALIE_SIGMA_COEFFICIENTS
        return intercept + slope * mu

    # TODO(P3): goalies fall through to the skater curve until the fitted
    # goalie coefficients land in GOALIE_SIGMA_COEFFICIENTS. A goalie's
    # outcome distribution is wider and start uncertainty adds a Bernoulli
    # term on top, so this understates them.
    return SKATER_SIGMA_INTERCEPT + SKATER_SIGMA_SLOPE * mu


def team_sigma(per_game_fpts: Sequence[float]) -> float:
    """Sigma of a total built from many player-games.

    Games are treated as independent, so variances add. That assumption was
    measured, not assumed: see `LINEMATE_CORRELATION`.
    """
    return sum(game_sigma(fpts, PlayerType.SKATER) ** 2 for fpts in per_game_fpts) ** 0.5


# `week/light.py` and `tests/optimize/matchup/test_win_probability.py` import
# this name.
compute_team_sigma = team_sigma

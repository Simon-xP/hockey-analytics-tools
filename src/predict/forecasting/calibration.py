"""Bias correction on the fantasy-point projection.

The situation-split model is well ranked but not well calibrated: measured
over 10,036 skater-games it over-projects fantasy points by about 19%. That is
not a TOI problem (predicted TOI lands within 3% of actual) — it is in the
per-60 rates, and it is broad: assists +27%, goals +19%, blocks +19%, hits
+13%, shots +12%.

An uncorrected mean is survivable for ranking, because a roughly
multiplicative bias mostly cancels when you compare two players. It is not
survivable for `P(win)`, which compares a projected total against an
opponent's projected total and reads the gap in units of sigma. With the bias
in place, an 80% prediction interval covers only 52% of actual weekly totals.
Correcting it brings coverage to the nominal 80%.

So this applies a fitted affine map to `fpts` at the end of the forecast:

    calibrated = CALIBRATION_INTERCEPT + CALIBRATION_SLOPE * raw

Fitted by `scripts/fit_variance_model.py`, which prints these constants
alongside the sigma curve they have to agree with. Refit both together: the
variance model in `src/optimize/week/variance.py` is fitted against
*calibrated* projections, so changing one without the other silently
decalibrates `P(win)`.

This is a patch over a model that should be fixed at source. It sits in one
place, behind one function, and reports its own provenance so that when the
per-60 rates are retrained the right move is obvious: refit, and if the raw
projection comes back unbiased, set the constants to (0.0, 1.0).
"""

from __future__ import annotations

# Fitted 2026-08-03 on season 20252026. See the module docstring and
# `scripts/fit_variance_model.py`.
CALIBRATION_INTERCEPT = 0.0
CALIBRATION_SLOPE = 1.0

# Season the constants were fitted on, for provenance in logs and reports.
CALIBRATION_SEASON = "20252026"


def calibrate_fpts(raw_fpts: float) -> float:
    """Map a raw model projection onto the calibrated scale.

    Clamped at zero: the affine fit can go slightly negative for a projection
    near zero, and a negative fantasy-point expectation is not meaningful for
    a skater under this scoring system.
    """
    return max(0.0, CALIBRATION_INTERCEPT + CALIBRATION_SLOPE * float(raw_fpts))


def is_calibrated() -> bool:
    """Whether a real correction is in force, as opposed to the identity."""
    return (CALIBRATION_INTERCEPT, CALIBRATION_SLOPE) != (0.0, 1.0)

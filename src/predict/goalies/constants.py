"""Constants for the goalie per-start model and its variance.

Every number here was measured from `goalie_game_log` rather than assumed.
The scripts that derive them are named alongside each one so they can be
re-derived when the data moves.

See `docs/plans/weekly-optimizer/04a-goalie-variance.md`.
"""

# ======================================================================
# Lookback window
# ======================================================================
# How much history feeds every rate in the model. Three seasons.
#
# This is not a tuning knob, it is an era correction. The league moves:
# across the eleven seasons on record, shots against per start fell from
# 28.7 to 27.0, save rate fell from .9154 to .8959, and fantasy points per
# start fell from 6.10 to 4.99. A goalie's shots from 2016 are evidence
# about a different league.
#
# Measured cost of getting this wrong: with an unbounded window the model
# over-projects the 2024-26 seasons by 0.93 points per start, because the
# accumulated history is drawn from a higher-scoring era. Every goalie
# looks better than they are, and goalie streams get systematically
# overvalued against skaters.
#
# The same window must be used by the live path and by the walk-forward
# fitter, or the fitted variance describes a model that is not the one
# being served. `lookback_start` is shared by both for exactly that reason.
LOOKBACK_SEASONS = 3

# ======================================================================
# Scoring algebra
# ======================================================================
# Fantasy points for a start, rearranged so it is expressed in the two
# quantities the model actually predicts (shots faced and goals allowed)
# rather than in saves:
#
#   FPTS = SV*w_sv + GA*w_ga + W*w_win + SO*w_so
#        = (SA - GA)*w_sv + GA*w_ga + ...
#        = SA*w_sv - GA*(w_sv - w_ga) + ...
#
# With the league's weights (SV 0.28, GA -1.25) that gives 0.28 per shot
# faced and -1.53 per goal allowed. A goal costs the 1.25 penalty *plus*
# the 0.28 save it denied, which is why the coefficient is not just 1.25.
# Derived in `src/predict/goalies/start_value.py::_points_per_shot`.

# ======================================================================
# Save-rate shrinkage
# ======================================================================
# Credibility constant: shots faced at which a goalie's own save rate earns
# 50% weight against the team-and-league baseline. Exposure is shots, not
# starts, because a 40-shot night is more evidence than a 20-shot night.
#
# Derived by variance decomposition on per-goalie-season save rates:
#
#     var_observed = var_true + var_binomial
#     k = p(1 - p) / var_true
#
# Measured per season (goalies with 300+ shots faced), 2015-16 to 2025-26:
#
#     season      lg sv%   sd_obs   sd_binom   sd_true   k (shots)
#     2015-16     .9158    .0082     .0088       ~0       undefined
#     2018-19     .9105    .0104     .0086      .0058     2408
#     2021-22     .9089    .0106     .0090      .0056     2634
#     2022-23     .9047    .0138     .0095      .0100      864
#     2023-24     .9050    .0109     .0093      .0056     2729
#     2024-25     .9007    .0119     .0094      .0073     1665
#     2025-26     .8962    .0122     .0098      .0071     1825
#
# Two things worth absorbing from that table. First, in 2015-16 the spread
# across goalies was *smaller* than binomial sampling noise, so there was
# no measurable talent spread at all and k is undefined. Second, true-talent
# sd sits around .005 to .007 against sampling noise of about .009: the
# noise band is wider than the talent band, exactly as the brief states.
#
# The value below is the median of the last five seasons, which is the
# regime being forecast. Older seasons imply even heavier shrinkage.
#
# Sanity check the brief asks for: after 50 starts (~1400 shots) a goalie's
# own save rate earns 1400/(1400+1800) = 0.44 weight, under the one-half
# ceiling. After 10 starts (~280 shots) it earns 0.13, which is why ten
# elite starts move a projection barely at all. That is correct behaviour,
# not excessive conservatism.
#
# Re-derive with: python -m scripts.fit_goalie_variance --derive-shrinkage
SAVE_RATE_CREDIBILITY_SHOTS = 1800.0

# Same idea for team-level rates, which stabilise faster because a team
# plays every night and the quantity is a team property rather than an
# individual one.
TEAM_RATE_CREDIBILITY_SHOTS = 900.0
TEAM_RATE_CREDIBILITY_GAMES = 25.0

# Starts of the current season before the season-to-date league level fully
# displaces the multi-season one.
#
# The league *level* (shots per start, save rate, goals per game) must come
# from the current season, not the lookback window, because it drifts
# monotonically and a trailing mean always lags a trend. It also stabilises
# fast: every league rate pools all 32 teams, so a few hundred starts is
# plenty. Team and goalie factors keep the longer window, because those are
# expressed as ratios and deltas against the league and so are era-neutral.
#
# Swept against measured bias on 2024-26 walk-forward. Lower is better here
# and the residual standard deviation is flat throughout, so there is no
# variance cost to trusting the current season quickly:
#
#     k     bias     resid sd    league level error
#     400   -0.259   4.107       +0.823 shots
#     200   -0.214   4.107       +0.646
#     100   -0.182   4.107       +0.521
#      50   -0.160   4.108       +0.440
#      25   -0.147   4.108       +0.389
#      10   -0.138   4.108       +0.352
#
# It plateaus around -0.14 rather than reaching zero, because shots against
# also drift *within* a season and a season-to-date mean lags that too. That
# residue is worth about 0.1 fantasy points and is handled by the
# calibration offset below rather than by modelling intra-season trend,
# which at this effect size would be fitting noise.
#
# 50 starts is roughly one week of games: enough to stop leaning on last
# season, not so few that the level is noisy.
LEAGUE_LEVEL_CREDIBILITY_STARTS = 50.0

# ======================================================================
# Calibration offset
# ======================================================================
# Additive correction applied to every per-start projection, in fantasy
# points. Same idea as `SituationModel.calibrate` for skaters: correct the
# systematic component rather than leave it in.
#
# After fixing the three structural errors (era-relative factors, a
# symmetric win model, team-basis shutouts) a small over-projection remains,
# caused by shots against drifting *within* a season faster than a
# season-to-date mean can track.
#
# Fitted on 2024-25 alone and verified on 2025-26, so it is an
# out-of-sample correction rather than a curve fit:
#
#     fit     2024-25   n=2622   bias -0.1422
#     verify  2025-26   n=2559   bias -0.1789  ->  -0.0367 after offset
#     pooled                                        -0.0181 after offset
#
# Re-fit whenever the mean model changes. A stale offset is worse than none.
START_VALUE_OFFSET = -0.142
START_VALUE_OFFSET_FITTED_ON = "20242025, verified on 20252026"

# ======================================================================
# Win probability
# ======================================================================
# Pythagorean exponent for hockey. The 2.0-2.2 range is well established
# in public work; 2.05 is the middle of it and the fit is flat nearby.
PYTHAGOREAN_EXPONENT = 2.05

# Home teams win about 54% of games. Applied as a multiplicative nudge to
# expected goals for rather than a post-hoc probability shift, so it stays
# consistent with the Pythagorean form.
HOME_GOALS_MULTIPLIER = 1.04

# A starting goalie does not collect every win their team earns: they can
# be pulled, and the reliever takes the decision. Measured across 25,550
# starts, starters take the decision in a win about 98% of the time.
STARTER_DECISION_RATE = 0.98

# ======================================================================
# Outcome variance
# ======================================================================
# Variance of actual fantasy points around the model's projection, given a
# start. A single global constant, deliberately: see the module docstring
# of `src/predict/goalies/variance.py` for why a per-goalie or
# projection-dependent form is the wrong shape.
#
# Fitted by `scripts/fit_goalie_variance.py` on walk-forward out-of-sample
# projections. The value and the season it came from are recorded there.
# Measured residual standard deviation is 4.11 fantasy points, against a
# mean of 5.1. The spread is comparable to the entire expected value, which
# is the fact that makes goalie streams behave differently from skater
# streams and the reason this constant has to exist at all.
#
# Residual variance by projection quintile, on the fitted model:
#     Q1 16.46   Q2 16.72   Q3 16.57   Q4 17.51   Q5 17.07
# The affine fit through those gives a slope of 0.070 with a bootstrap 95%
# CI of [-0.079, 0.212]. Zero is comfortably inside it, so a constant is the
# right shape and the affine form was discarded as the brief instructs.
OUTCOME_VAR = 16.87
OUTCOME_VAR_FITTED_ON = "20242025 and 20252026, walk-forward, 5,181 starts"

# ======================================================================
# Fallbacks
# ======================================================================
# Used when a goalie or team has no prior history at all, e.g. a debut.
# These are league averages, and the confidence attached to a projection
# built on them should be low.
DEFAULT_SHOTS_AGAINST_PER_START = 28.0
DEFAULT_SAVE_RATE = 0.900
DEFAULT_GOALS_FOR_PER_GAME = 3.05

# Minimum shots faced before a goalie's own rate is allowed to move the
# projection at all. Below this the credibility weight is negligible
# anyway; the floor just avoids noise in the reported diagnostics.
MIN_SHOTS_FOR_INDIVIDUAL_RATE = 50


def lookback_start(as_of, seasons: int = LOOKBACK_SEASONS):
    """First date whose games are visible to a projection made at `as_of`.

    Season-aligned to 1 August so the window contains whole seasons rather
    than cutting one in half. Shared by the live path and the walk-forward
    fitter so the fitted variance describes the model actually being served.
    """
    from datetime import date as _date

    base_year = as_of.year if as_of.month >= 8 else as_of.year - 1
    return _date(base_year - seasons, 8, 1)

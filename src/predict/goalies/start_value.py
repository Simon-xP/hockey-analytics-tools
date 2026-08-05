"""Expected fantasy points for a goalie, given that they start.

`start_value` in the optimizer's contract. This is the mean only; the
spread lives in `variance.py`.

## Shape of the model

Fantasy points for a start decompose into four terms with very different
signal, so each is projected separately and then combined:

    FPTS = 0.28 * shots_against
         - 1.53 * goals_against
         + 3.30 * P(win)
         + 2.30 * P(shutout)

Measured contributions per start in 2024-25: saves +6.89, goals against
-3.39, wins +1.62, shutouts +0.15.

Three of those four terms are mostly **not about the goalie**:

- Shots against comes from the opponent's offense and the team's defensive
  structure. It is also the most predictable thing on the list: split-half
  reliability within a season is 0.45, against 0.09 for save percentage.
- Wins come from **the goalie's own team scoring goals**. This is why
  `own_gf_per_game` is an input and not an oversight. A model reading only
  opponent strength systematically undervalues good goalies on high-scoring
  teams and overvalues them on defensive ones, and biases streaming toward
  the wrong teams all season.
- Shutouts are not modelled separately at all. They fall out of the goals
  against distribution as `exp(-E[GA])`, which is the Poisson probability
  of zero. At a league-typical 2.8 expected goals that gives 6.1% against
  an observed 6.5%, close enough that a separate model would be fitting
  noise.

Only the save-rate term is a goalie property, and it is shrunk hard before
it is allowed to move anything. See `save_quality.py`.

## Multiplicative rate structure

Shots against and goals for are both built as a league level scaled by two
opposing factors:

    E[shots against] = league_level * team_defense_factor * opp_offense_factor

This is the standard log-linear rate form. It keeps the league level
explicit, which matters because the league level moves: shots against per
start fell from 28.7 to 27.0 and save rate from .9154 to .8959 across the
eleven seasons on record. A model with the era baked into its coefficients
would drift.

## Purity

`project_start_value` takes a plain `StartInputs` and touches no database.
The walk-forward fitter in `scripts/fit_goalie_variance.py` and the live
path in `forecast_start_value` both call it, so there is one scoring core
and no train/serve skew. All temporal gating happens while *assembling*
`StartInputs`, never inside the scoring.
"""

import math
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text

from src.core.scoring import GOALIE_WEIGHTS
from src.predict.goalies.constants import (
    DEFAULT_GOALS_FOR_PER_GAME,
    DEFAULT_SAVE_RATE,
    DEFAULT_SHOTS_AGAINST_PER_START,
    HOME_GOALS_MULTIPLIER,
    LEAGUE_LEVEL_CREDIBILITY_STARTS,
    LOOKBACK_SEASONS,
    PYTHAGOREAN_EXPONENT,
    START_VALUE_OFFSET,
    STARTER_DECISION_RATE,
    TEAM_RATE_CREDIBILITY_GAMES,
    lookback_start,
)
from src.predict.goalies.save_quality import (
    SaveQuality,
    credibility_weight,
    estimate_save_quality,
)

# Points per shot faced, and the cost of a goal allowed. See constants.py
# for the algebra: a goal costs the goals-against penalty plus the save it
# denied, so the two are not independent coefficients.
POINTS_PER_SHOT = GOALIE_WEIGHTS["saves"]
POINTS_PER_GOAL_ALLOWED = GOALIE_WEIGHTS["goals_against"] - GOALIE_WEIGHTS["saves"]


@dataclass(frozen=True)
class StartInputs:
    """Everything the per-start projection needs, already temporally gated.

    Assembled either from SQL (`forecast_start_value`) or from running
    accumulators (the walk-forward fitter). Once built, scoring is pure.
    """

    # Workload. `league_sa_per_start` is the level *now*;
    # `window_sa_per_start` is the level over the span the team rates were
    # measured in. Both are needed: the factors must be ratios against
    # their own era, and only then rescaled to the current level.
    team_sa_per_start: float      # goalie's team, shots allowed per start
    opp_sf_per_game: float        # opponent's shots generated per game
    league_sa_per_start: float
    window_sa_per_start: float

    # Shot suppression quality
    save_rate: float              # shrunk, from save_quality
    league_save_rate: float

    # Scoring environment. own_gf_per_game is what wires the win component
    # to the goalie's own offense.
    own_gf_per_game: float
    opp_ga_per_game: float
    # Team goals *allowed*, for the win model's denominator. Needed
    # separately from the goalie's own goals against, which excludes
    # empty-net goals and so sits about 0.2 below the team number.
    own_ga_per_game: float
    opp_gf_per_game: float
    league_gf_per_game: float
    window_gf_per_game: float

    is_home: bool = False

    # Diagnostics, not used in scoring.
    save_credibility: float = 0.0
    team_games_seen: int = 0


@dataclass(frozen=True)
class StartProjection:
    """Projected mean outcome of one start, with its components."""

    start_value: float            # expected fantasy points
    shots_against: float
    goals_against: float
    win_prob: float
    shutout_prob: float

    def component_points(self) -> dict[str, float]:
        """Points contributed by each term, for explanation and debugging."""
        return {
            "saves": POINTS_PER_SHOT * self.shots_against,
            "goals_against": POINTS_PER_GOAL_ALLOWED * self.goals_against,
            "wins": GOALIE_WEIGHTS["wins"] * self.win_prob,
            "shutouts": GOALIE_WEIGHTS["shutouts"] * self.shutout_prob,
        }


def _safe_factor(numerator: float, denominator: float, lo: float = 0.6,
                 hi: float = 1.6) -> float:
    """A rate ratio, clamped so a thin sample cannot produce a wild factor."""
    if denominator <= 0 or numerator <= 0:
        return 1.0
    return max(lo, min(hi, numerator / denominator))


def project_start_value(inputs: StartInputs) -> StartProjection:
    """Expected fantasy points given a start. Pure."""
    league_sa = inputs.league_sa_per_start or DEFAULT_SHOTS_AGAINST_PER_START
    window_sa = inputs.window_sa_per_start or league_sa

    # --- Shots faced -------------------------------------------------
    # Current league level, scaled by how leaky this team is and how much
    # volume this opponent generates.
    #
    # Both factors are ratios against the *window* level, not the current
    # one. That is what makes them era-neutral and safe to multiply. Using
    # the current level as the denominator while the numerators come from a
    # higher-shot window inflates each factor, and the error compounds
    # because there are two of them: measured over-projection of 3.1 shots
    # per start, worth +0.88 fantasy points on every goalie at once.
    team_def = _safe_factor(inputs.team_sa_per_start, window_sa)
    opp_off = _safe_factor(inputs.opp_sf_per_game, window_sa)
    shots_against = league_sa * team_def * opp_off

    # --- Goals allowed -----------------------------------------------
    save_rate = inputs.save_rate or DEFAULT_SAVE_RATE
    goals_against = max(0.05, shots_against * (1.0 - save_rate))

    # --- Win probability ---------------------------------------------
    # Pythagorean on expected team goals for and against. Own-team offense
    # is the dominant input; the opponent's defence scales it.
    #
    # Both sides must be on the *team* scoring basis. Using the goalie's
    # projected goals against as the denominator looks natural and is
    # wrong: goalie goals against excludes empty-net goals, so it sits
    # about 0.2 below what the team actually conceded. Comparing team goals
    # for against goalie goals against is asymmetric and inflated every win
    # probability by about 6 points, worth +0.20 fantasy points per start.
    league_gf = inputs.league_gf_per_game or DEFAULT_GOALS_FOR_PER_GAME
    window_gf = inputs.window_gf_per_game or league_gf

    own_off = _safe_factor(inputs.own_gf_per_game, window_gf)
    opp_def = _safe_factor(inputs.opp_ga_per_game, window_gf)
    expected_gf = league_gf * own_off * opp_def
    if inputs.is_home:
        expected_gf *= HOME_GOALS_MULTIPLIER

    own_def = _safe_factor(inputs.own_ga_per_game, window_gf)
    opp_off_goals = _safe_factor(inputs.opp_gf_per_game, window_gf)
    expected_team_ga = league_gf * own_def * opp_off_goals

    e = PYTHAGOREAN_EXPONENT
    team_win_prob = (expected_gf**e
                     / (expected_gf**e + expected_team_ga**e))

    # The starter does not bank every win the team earns; a pulled goalie
    # hands the decision to the reliever.
    win_prob = team_win_prob * STARTER_DECISION_RATE

    # --- Shutout ------------------------------------------------------
    # Poisson zero, derived rather than modelled.
    #
    # Taken on the *team* goals against, not the goalie's. A shutout needs
    # the team to concede nothing: an empty-net goal breaks it even when
    # the goalie themselves allowed zero. Using the goalie's number
    # overstates shutouts by about a third (0.066 against an observed
    # 0.050), because it is the probability of a different event.
    shutout_prob = math.exp(-expected_team_ga)

    start_value = (
        POINTS_PER_SHOT * shots_against
        + POINTS_PER_GOAL_ALLOWED * goals_against
        + GOALIE_WEIGHTS["wins"] * win_prob
        + GOALIE_WEIGHTS["shutouts"] * shutout_prob
        # Corrects a small residual over-projection from intra-season
        # drift. See START_VALUE_OFFSET for the derivation.
        + START_VALUE_OFFSET
    )

    return StartProjection(
        start_value=start_value,
        shots_against=shots_against,
        goals_against=goals_against,
        win_prob=win_prob,
        shutout_prob=shutout_prob,
    )


# ======================================================================
# Database-backed assembly
# ======================================================================

def _rates_between(session, as_of: date, since: date):
    """League shots per start, save rate, goals per game over a span."""
    row = session.execute(
        text("""
            SELECT AVG(shots_against)::float,
                   (SUM(saves)::float / NULLIF(SUM(shots_against), 0)),
                   AVG(opponent_score)::float,
                   COUNT(*)
            FROM goalie_game_log
            WHERE is_start AND game_date < :as_of AND game_date >= :since
        """),
        {"as_of": as_of, "since": since},
    ).fetchone()
    if not row or row[0] is None:
        return None
    return (float(row[0]), float(row[1] or DEFAULT_SAVE_RATE),
            float(row[2] or DEFAULT_GOALS_FOR_PER_GAME), int(row[3]))


def _league_rates(
    session, as_of: date, since: date,
) -> tuple[float, float, float, float]:
    """Current league level, plus the window save rate for era adjustment.

    Returns (shots_per_start, save_rate, goals_per_game, window_levels)
    where `window_levels` is (shots_per_start, save_rate, goals_per_game)
    over the lookback window.

    The first three describe the league *now*, blended from season-to-date
    toward the multi-season window while the season is young. The window
    triple says which era the team and goalie rates were earned in, so
    their factors can be formed against the right denominator.
    """
    window = _rates_between(session, as_of, since)
    if window is None:
        d = (DEFAULT_SHOTS_AGAINST_PER_START, DEFAULT_SAVE_RATE,
             DEFAULT_GOALS_FOR_PER_GAME)
        return (*d, d)

    window_levels = (window[0], window[1], window[2])

    season_start = lookback_start(as_of, 0)
    season = _rates_between(session, as_of, season_start)
    if season is None:
        return (*window_levels, window_levels)

    z = credibility_weight(season[3], LEAGUE_LEVEL_CREDIBILITY_STARTS)
    return (
        z * season[0] + (1 - z) * window[0],
        z * season[1] + (1 - z) * window[1],
        z * season[2] + (1 - z) * window[2],
        window_levels,
    )


def _team_rates(session, team_id: int, as_of: date, since: date) -> dict:
    """A team's own shots allowed, shots generated, and goals for/against.

    `shots_against` on a row is what the goalie faced, so aggregating by
    `team_id` gives the team's defensive volume and aggregating by
    `opponent_team_id` gives the volume that team generated.
    """
    row = session.execute(
        text("""
            SELECT
                AVG(shots_against) FILTER (WHERE team_id = :tid),
                COUNT(*)           FILTER (WHERE team_id = :tid),
                AVG(team_score)    FILTER (WHERE team_id = :tid),
                AVG(opponent_score) FILTER (WHERE team_id = :tid),
                AVG(shots_against) FILTER (WHERE opponent_team_id = :tid)
            FROM goalie_game_log
            WHERE is_start AND game_date < :as_of AND game_date >= :since
        """),
        {"tid": team_id, "as_of": as_of, "since": since},
    ).fetchone()

    return {
        "sa_per_start": float(row[0]) if row and row[0] is not None else None,
        "games": int(row[1]) if row and row[1] else 0,
        "gf_per_game": float(row[2]) if row and row[2] is not None else None,
        "ga_per_game": float(row[3]) if row and row[3] is not None else None,
        "sf_per_game": float(row[4]) if row and row[4] is not None else None,
    }


def _blend_toward_league(value: float | None, league: float, games: int) -> float:
    """Credibility-blend a team rate toward the league level."""
    if value is None:
        return league
    z = credibility_weight(games, TEAM_RATE_CREDIBILITY_GAMES)
    return z * value + (1 - z) * league


def build_start_inputs(
    session,
    nhl_id: int,
    team_id: int,
    opponent_team_id: int,
    as_of: date,
    is_home: bool,
    lookback_seasons: int = LOOKBACK_SEASONS,
    save_quality: SaveQuality | None = None,
) -> StartInputs:
    """Assemble `StartInputs` from the database, gated strictly at `as_of`.

    Every query here filters `game_date < :as_of`. That is the single point
    where time discipline is enforced for the mean model.
    """
    since = lookback_start(as_of, lookback_seasons)

    lg_sa, lg_save, lg_gf, window = _league_rates(session, as_of, since)
    window_sa, window_save, window_gf = window
    own = _team_rates(session, team_id, as_of, since)
    opp = _team_rates(session, opponent_team_id, as_of, since)

    if save_quality is None:
        save_quality = estimate_save_quality(
            session, nhl_id, team_id, as_of, lookback_seasons,
            current_league_rate=lg_save,
        )

    return StartInputs(
        team_sa_per_start=_blend_toward_league(
            own["sa_per_start"], window_sa, own["games"]),
        opp_sf_per_game=_blend_toward_league(
            opp["sf_per_game"], window_sa, opp["games"]),
        league_sa_per_start=lg_sa,
        window_sa_per_start=window_sa,
        save_rate=save_quality.save_rate,
        league_save_rate=lg_save,
        own_gf_per_game=_blend_toward_league(
            own["gf_per_game"], window_gf, own["games"]),
        opp_ga_per_game=_blend_toward_league(
            opp["ga_per_game"], window_gf, opp["games"]),
        own_ga_per_game=_blend_toward_league(
            own["ga_per_game"], window_gf, own["games"]),
        opp_gf_per_game=_blend_toward_league(
            opp["gf_per_game"], window_gf, opp["games"]),
        league_gf_per_game=lg_gf,
        window_gf_per_game=window_gf,
        is_home=is_home,
        save_credibility=save_quality.credibility,
        team_games_seen=own["games"],
    )


def forecast_start_value(
    session,
    nhl_id: int,
    team_id: int,
    opponent_team_id: int,
    as_of: date,
    is_home: bool,
) -> StartProjection:
    """Expected fantasy points if this goalie starts. Live entry point."""
    return project_start_value(build_start_inputs(
        session, nhl_id, team_id, opponent_team_id, as_of, is_home,
    ))

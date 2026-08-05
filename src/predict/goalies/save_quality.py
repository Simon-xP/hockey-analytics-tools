"""Empirical Bayes shrinkage for goalie save rate.

Same credibility machinery as `src/predict/forecasting/empirical_bayes.py`
uses for rare skater events, applied to the one goalie quantity that is
genuinely a property of the goalie rather than of the game in front of them.

## Why shrinkage is not optional here

A goalie's save rate over a short run is close to pure noise. Measured on
`goalie_game_log`, the spread in save rate across goalies within a season
has a true-talent standard deviation of roughly .005 to .007, while the
binomial sampling noise on a season's worth of shots is about .009. In
2015-16 the observed spread was *smaller* than the sampling noise, meaning
no talent spread was detectable at all.

An unshrunk model reading trailing save percentage is therefore fitting
randomness, and will be confidently wrong about which goalie to stream. The
credibility weight below is what stops that.

## The shrinkage chain

Two stages, so the prior is a team-and-league baseline rather than league
alone:

    baseline = z_team * team_rate   + (1 - z_team) * league_rate
    estimate = z_goalie * own_rate  + (1 - z_goalie) * baseline

with `z = shots / (shots + k)` at each stage. Exposure is **shots faced**,
not starts: a 40-shot night carries more evidence than a 20-shot night, and
counting starts throws that away.

The team stage matters because a goalie's raw save rate partly reflects the
shot quality their team concedes. Regressing a backup on a leaky team
toward the league mean alone would overrate them.

With `SAVE_RATE_CREDIBILITY_SHOTS = 1800`, a goalie's own rate earns 0.13
weight after ten starts and 0.44 after fifty. See `constants.py` for the
derivation and the per-season table it came from.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text

from src.predict.goalies.constants import (
    DEFAULT_SAVE_RATE,
    LOOKBACK_SEASONS,
    SAVE_RATE_CREDIBILITY_SHOTS,
    TEAM_RATE_CREDIBILITY_SHOTS,
    lookback_start,
)


@dataclass(frozen=True)
class SaveQuality:
    """A goalie's shrunk save rate and the evidence behind it."""

    save_rate: float          # shrunk estimate, the number to use
    raw_save_rate: float      # unshrunk, for diagnostics only
    league_rate: float
    team_rate: float
    shots_faced: int          # the goalie's own exposure
    credibility: float        # z in [0, 1], weight on their own rate

    @property
    def shrinkage_applied(self) -> float:
        """How far the estimate moved from the raw rate toward baseline."""
        return abs(self.save_rate - self.raw_save_rate)


def credibility_weight(exposure: float, k: float) -> float:
    """Standard credibility weight z = n / (n + k)."""
    if exposure <= 0:
        return 0.0
    return exposure / (exposure + k)


def shrink_save_rate(
    goalie_shots: float,
    goalie_saves: float,
    team_shots: float,
    team_saves: float,
    league_rate: float,
    current_league_rate: float | None = None,
    k_goalie: float = SAVE_RATE_CREDIBILITY_SHOTS,
    k_team: float = TEAM_RATE_CREDIBILITY_SHOTS,
) -> SaveQuality:
    """Shrink a save rate toward a team-then-league baseline, era-adjusted.

    Shrinkage happens in **delta space**: how far above or below the league
    a goalie was, rather than their absolute rate. The shrunk delta is then
    applied to the *current* league rate.

    This matters because the league moves under the window. Save rate fell
    from .9154 to .8959 across the eleven seasons on record, and by about 9
    points inside a typical three-season window. Shrinking absolute rates
    would carry a goalie's .910 from two seasons ago into a .896 league and
    quietly under-predict goals against, which over-projects fantasy points
    for every goalie at once. Measured cost of the naive version on 2024-26
    was a 0.76 point per start over-projection.

    Args:
        league_rate: league save rate over the same window the goalie's
            shots were drawn from. The era the raw numbers were earned in.
        current_league_rate: league save rate right now. Defaults to
            `league_rate`, which is the correct behaviour when the window
            is the current season.

    Pure function: no database access, so the walk-forward fitter and the
    live path share exactly this code and cannot drift apart.
    """
    current = (current_league_rate if current_league_rate is not None
               else league_rate)

    raw = goalie_saves / goalie_shots if goalie_shots > 0 else league_rate
    team_raw = team_saves / team_shots if team_shots > 0 else league_rate

    # Stage one: how much better than league is this team's crease, shrunk
    # toward no difference.
    z_team = credibility_weight(team_shots, k_team)
    baseline_delta = z_team * (team_raw - league_rate)

    # Stage two: how much better than that is this goalie, shrunk toward
    # the team baseline.
    z_goalie = credibility_weight(goalie_shots, k_goalie)
    delta = (z_goalie * (raw - league_rate)
             + (1 - z_goalie) * baseline_delta)

    return SaveQuality(
        save_rate=current + delta,
        raw_save_rate=raw,
        league_rate=current,
        team_rate=team_raw,
        shots_faced=int(goalie_shots),
        credibility=z_goalie,
    )


def estimate_save_quality(
    session,
    nhl_id: int,
    team_id: int,
    as_of: date,
    lookback_seasons: int = LOOKBACK_SEASONS,
    current_league_rate: float | None = None,
) -> SaveQuality:
    """Database-backed save quality for one goalie, gated at `as_of`.

    Only games strictly before `as_of` are visible. The window is capped at
    `lookback_seasons` because league save rate drifts (it fell about 20
    points across the eleven seasons on record), so a goalie's decade-old
    shots are evidence about a different league.
    """
    cutoff_season_start = lookback_start(as_of, lookback_seasons)

    row = session.execute(
        text("""
            SELECT
                COALESCE(SUM(shots_against) FILTER (WHERE goalie_id = :gid), 0),
                COALESCE(SUM(saves)         FILTER (WHERE goalie_id = :gid), 0),
                COALESCE(SUM(shots_against) FILTER (WHERE team_id = :tid), 0),
                COALESCE(SUM(saves)         FILTER (WHERE team_id = :tid), 0),
                COALESCE(SUM(shots_against), 0),
                COALESCE(SUM(saves), 0)
            FROM goalie_game_log
            WHERE game_date < :as_of AND game_date >= :since AND is_start
        """),
        {"gid": nhl_id, "tid": team_id, "as_of": as_of,
         "since": cutoff_season_start},
    ).fetchone()

    if not row or not row[4]:
        return SaveQuality(
            save_rate=DEFAULT_SAVE_RATE, raw_save_rate=DEFAULT_SAVE_RATE,
            league_rate=DEFAULT_SAVE_RATE, team_rate=DEFAULT_SAVE_RATE,
            shots_faced=0, credibility=0.0,
        )

    goalie_shots, goalie_saves, team_shots, team_saves, lg_shots, lg_saves = row
    league_rate = lg_saves / lg_shots if lg_shots else DEFAULT_SAVE_RATE

    return shrink_save_rate(
        goalie_shots=float(goalie_shots), goalie_saves=float(goalie_saves),
        team_shots=float(team_shots), team_saves=float(team_saves),
        league_rate=league_rate, current_league_rate=current_league_rate,
    )

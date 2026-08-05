"""Penalty minutes projection.

PIM is worth 0.3 in the league's scoring (`src/core/scoring.py`) but the
situation-split model does not predict it: `project_per_game` covers goals,
assists, shots, hits, and blocks only. That left a real scoring category
worth about 0.14 FPTS per game silently valued at zero, and considerably more
than that for the players who actually take penalties.

This is deliberately the simplest thing that works. PIM is close to a fixed
personal trait — an agitator takes penalties every year, a skill winger does
not — so a career rate shrunk toward the league mean captures nearly all the
signal. No features, no model, no training step.

    pim_per_game = (career_penalties + PRIOR_GAMES * league_rate)
                   / (career_games + PRIOR_GAMES)
                   * PIM_PER_PENALTY

Shrinkage rather than a games-played threshold: a rookie with four games gets
mostly the league rate, a veteran with six hundred gets essentially their own,
and there is no cliff in between. A player with no history at all falls back
to the league rate exactly, which is the behaviour a hard fallback branch
would have given anyway.

Constants measured 2026-08-03 against `game_advanced_stats`:

- `PIM_PER_PENALTY = 2.41`. Penalties are stored as counts, not minutes. Most
  are two-minute minors, but majors, double-minors, and misconducts pull the
  average up, so a flat x2 understates PIM by about 17%. The factor is fitted
  against Natural Stat Trick's actual `pim_per_60` over the 4,914 player-games
  where both sources overlap.
- `LEAGUE_PENALTIES_PER_GAME = 0.188`, over seasons 2021-22 onward. That works
  out to 0.45 PIM and 0.14 FPTS per game.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text

PIM_PER_PENALTY = 2.4081
LEAGUE_PENALTIES_PER_GAME = 0.1879

# Games of league-average prior mixed into every player's career rate. Low
# enough that a regular's own rate dominates, high enough that a call-up is
# not defined by one slashing minor.
PRIOR_GAMES = 25


def project_pim_per_game(
    session,
    nhl_id: int,
    as_of: date | None = None,
) -> float:
    """Expected penalty minutes for one game.

    Args:
        session: DB session.
        nhl_id: NHL player ID.
        as_of: Knowledge cutoff. Only games strictly before this date count,
            so a backtest never sees a penalty before it was taken.

    Returns:
        Expected PIM. Falls back to the league rate for a player with no
        history, never to zero.
    """
    row = session.execute(
        text(
            """
            SELECT COUNT(*), COALESCE(SUM(gas.penalties), 0)
            FROM game_advanced_stats gas
            JOIN games g ON gas.game_id = g.game_id
            WHERE gas.player_id = :nhl_id
                  AND gas.situation = 'all'
                  AND (:as_of IS NULL OR g.date < :as_of)
            """
        ),
        {"nhl_id": nhl_id, "as_of": as_of},
    ).fetchone()

    games = int(row[0] or 0)
    penalties = float(row[1] or 0.0)

    rate = (penalties + PRIOR_GAMES * LEAGUE_PENALTIES_PER_GAME) / (games + PRIOR_GAMES)
    return rate * PIM_PER_PENALTY


def league_pim_per_game() -> float:
    """The fallback every projection regresses toward."""
    return LEAGUE_PENALTIES_PER_GAME * PIM_PER_PENALTY

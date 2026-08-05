"""Build one stat line per goalie per game.

Inputs, all already in the database:
    shot_attempts   every shot, with `goalie_id`, `strength_state`, `xg`
    player_shifts   when each goalie was actually in the net
    game_events     the goal sequence, used to find the goalie of record
    games           final scores

Output: `GoalieGameRow` dicts ready to write into `goalie_game_log`.

The six correctness rules this module exists to enforce are documented on
the model in `src/core/models/goalie_stats.py`. The two that need real
logic rather than a filter are the goalie of record and the start flag.

## Goalie of record

NHL rule: the win goes to the goalie who was in the net when their team
scored the goal that gave them a lead they never gave back. That is the
game-winning goal, and it is the (losing_score + 1)-th goal scored by the
winning team. The loss goes to whichever goalie was in the opposing net at
that same moment, which is not necessarily the goalie who finished.

Shootout games are the exception. The winner's final score includes a goal
that was never scored in live play, so the game-winning-goal arithmetic has
nothing to point at. In those games the decision goes to whoever finished
overtime, and the loser gets an OTL.

## Start detection

A start is a shift beginning at 0:00 of period 1. Note that
`player_shifts.start_time` carries two formats depending on which ingest
path wrote the row (`'00:00'` from the JSON API, `'0:00'` from the HTML
fallback parser), so every comparison here goes through `time_to_seconds`
rather than matching strings.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text

from src.analytics.advanced_stats.shifts import time_to_seconds
from src.core.scoring import GOALIE_WEIGHTS

# Shots that reached the net. A goal is a shot that went in, so both count
# toward shots against.
ON_NET_EVENTS = ("shot-on-goal", "goal")
# Unblocked attempts, the wider workload measure.
FENWICK_EVENTS = ("shot-on-goal", "goal", "missed-shot")

# Regulation is periods 1-3, overtime is period 4. Period 5 is the
# shootout, which NHL scoring does not charge as saves or goals against.
MAX_SCORING_PERIOD = 4

PERIOD_SECONDS = 1200


@dataclass
class GoalieGameRow:
    """One goalie's line for one game, ready to persist."""

    game_id: int
    goalie_id: int
    team_id: int
    opponent_team_id: int
    game_date: date
    season: str

    is_start: bool = False
    is_relief: bool = False
    is_home: bool = False
    toi_seconds: int = 0
    played_full_game: bool = False

    decision: str | None = None
    shutout: bool = False
    team_score: int | None = None
    opponent_score: int | None = None

    shots_against: int = 0
    saves: int = 0
    goals_against: int = 0
    fenwick_against: int = 0
    empty_net_ga_team: int = 0

    ev_shots_against: int = 0
    ev_goals_against: int = 0
    pk_shots_against: int = 0
    pk_goals_against: int = 0
    pp_shots_against: int = 0
    pp_goals_against: int = 0

    fpts: float = 0.0

    def compute_fpts(self) -> float:
        """Fantasy points under the league's goalie weights.

        Only a win scores. A loss or an overtime loss is worth nothing in
        this league, so `decision` only matters when it is "W".
        """
        self.fpts = (
            self.saves * GOALIE_WEIGHTS["saves"]
            + self.goals_against * GOALIE_WEIGHTS["goals_against"]
            + (GOALIE_WEIGHTS["wins"] if self.decision == "W" else 0.0)
            + (GOALIE_WEIGHTS["shutouts"] if self.shutout else 0.0)
        )
        return self.fpts


# ======================================================================
# Helpers
# ======================================================================

def known_goalie_ids(session) -> set[int]:
    """Every player ID we have reason to believe is a goalie.

    Union of two sources, because neither alone is complete: `players`
    only holds current rosters (97 goalies), while `shot_attempts` only
    knows a goalie once they have faced a shot on net.
    """
    from_shots = session.execute(
        text("SELECT DISTINCT goalie_id FROM shot_attempts "
             "WHERE goalie_id IS NOT NULL")
    ).scalars().all()
    from_players = session.execute(
        text("SELECT nhl_id FROM players WHERE position = 'G'")
    ).scalars().all()
    return set(from_shots) | set(from_players)


def absolute_seconds(period: int, time_in_period: str) -> int:
    """Seconds elapsed since the opening faceoff."""
    return (period - 1) * PERIOD_SECONDS + time_to_seconds(time_in_period)


def _season_from_game_id(game_id: int) -> str:
    year = game_id // 1_000_000
    return f"{year}{year + 1}"


def _goalie_situation(strength_state: str | None) -> str:
    """Classify a shot from the goalie's point of view.

    `strength_state` is written shooter-first, so "5v4" means the shooting
    team has five skaters and the goalie's team has four. From the goalie's
    side that is a penalty kill.

    Returns "ev", "pk", or "pp". Anything unparseable is treated as even
    strength, which is where the overwhelming majority of shots live.
    """
    if not strength_state or "v" not in strength_state:
        return "ev"
    shooter, _, defender = strength_state.partition("v")
    try:
        shooter_n, defender_n = int(shooter), int(defender)
    except ValueError:
        return "ev"
    if defender_n < shooter_n:
        return "pk"
    if defender_n > shooter_n:
        return "pp"
    return "ev"


def load_goalie_shifts(
    session, game_id: int, goalie_ids: set[int],
) -> dict[int, list[tuple[int, int]]]:
    """Absolute-second intervals each goalie was on the ice.

    Returns goalie_id -> sorted list of (start, end) in game seconds.
    """
    rows = session.execute(
        text("""
            SELECT player_id, period, start_time, end_time
            FROM player_shifts
            WHERE game_id = :gid
            ORDER BY period, shift_number
        """),
        {"gid": game_id},
    ).fetchall()

    intervals: dict[int, list[tuple[int, int]]] = {}
    for player_id, period, start_time, end_time in rows:
        if player_id not in goalie_ids:
            continue
        start = absolute_seconds(period, start_time)
        end = absolute_seconds(period, end_time)
        # A shift recorded as ending before it starts is corrupt; skip it
        # rather than letting a negative duration into the TOI total.
        if end < start:
            continue
        intervals.setdefault(player_id, []).append((start, end))

    for spans in intervals.values():
        spans.sort()
    return intervals


def goalie_in_net_at(
    intervals: dict[int, list[tuple[int, int]]],
    candidates: set[int],
    moment: int,
) -> int | None:
    """Which of `candidates` was in the net at `moment` (game seconds).

    Falls back to the candidate whose shift ended most recently before the
    moment. That covers the empty-net case, where nobody is technically in
    the net but the goalie who was just pulled is still the one on the hook
    for the decision.
    """
    for goalie_id in candidates:
        for start, end in intervals.get(goalie_id, []):
            if start <= moment <= end:
                return goalie_id

    most_recent, best_end = None, None
    for goalie_id in candidates:
        for start, end in intervals.get(goalie_id, []):
            if end <= moment and (best_end is None or end > best_end):
                most_recent, best_end = goalie_id, end
    return most_recent


def load_goal_sequence(session, game_id: int) -> list[dict]:
    """Every goal in the game, in order, with its absolute timestamp.

    Read from `game_events` rather than `shot_attempts` so goals that never
    made it into the shot table (missing coordinates, for example) still
    count toward the score reconstruction.
    """
    rows = session.execute(
        text("""
            SELECT period, time_in_period, team_id, situation_code
            FROM game_events
            WHERE game_id = :gid AND event_type = 'goal'
                  AND period <= :maxp
            ORDER BY period, sort_order
        """),
        {"gid": game_id, "maxp": MAX_SCORING_PERIOD},
    ).fetchall()

    return [
        {
            "period": r[0],
            "seconds": absolute_seconds(r[0], r[1]),
            "team_id": r[2],
            "situation_code": r[3],
        }
        for r in rows
    ]


def went_to_shootout(session, game_id: int) -> bool:
    """Did this game reach a shootout?"""
    found = session.execute(
        text("SELECT 1 FROM game_events WHERE game_id = :gid AND period >= 5 "
             "LIMIT 1"),
        {"gid": game_id},
    ).scalar()
    return found is not None


def assign_decisions(
    goals: list[dict],
    intervals: dict[int, list[tuple[int, int]]],
    team_goalies: dict[int, set[int]],
    home_team_id: int,
    away_team_id: int,
    home_score: int,
    away_score: int,
    shootout: bool,
) -> dict[int, str]:
    """Work out which goalie gets the W, and which gets the L or OTL.

    Returns goalie_id -> "W" / "L" / "OTL". Goalies not in the result had
    no decision.
    """
    if home_score == away_score:
        return {}  # unfinished or bad data, no decision to hand out

    winner = home_team_id if home_score > away_score else away_team_id
    loser = away_team_id if winner == home_team_id else home_team_id
    losing_score = min(home_score, away_score)

    winner_goals = [g for g in goals if g["team_id"] == winner]

    if shootout:
        # No live-play goal corresponds to the shootout winner, so the
        # decision follows whoever finished overtime.
        moment = max(
            (end for spans in intervals.values() for _, end in spans),
            default=0,
        )
        loser_result = "OTL"
    else:
        # The game-winning goal is the winner's (losing_score + 1)-th.
        index = losing_score
        if index >= len(winner_goals):
            # Score and goal events disagree, usually a missing event.
            # Fall back to the last goal we do have.
            if not winner_goals:
                return {}
            gwg = winner_goals[-1]
        else:
            gwg = winner_goals[index]
        moment = gwg["seconds"]
        overtime = gwg["period"] >= 4
        loser_result = "OTL" if overtime else "L"

    decisions: dict[int, str] = {}
    winning_goalie = goalie_in_net_at(
        intervals, team_goalies.get(winner, set()), moment
    )
    losing_goalie = goalie_in_net_at(
        intervals, team_goalies.get(loser, set()), moment
    )
    if winning_goalie is not None:
        decisions[winning_goalie] = "W"
    if losing_goalie is not None:
        decisions[losing_goalie] = loser_result
    return decisions


# ======================================================================
# Main entry point
# ======================================================================

def build_goalie_rows(
    session,
    game_id: int,
    home_team_id: int,
    away_team_id: int,
    game_date: date,
    home_score: int | None,
    away_score: int | None,
    goalie_ids: set[int] | None = None,
) -> list[GoalieGameRow]:
    """Build every goalie's line for one game.

    Returns one row per goalie who actually played. A goalie who dressed as
    the backup and never entered gets no row, which is the right behaviour:
    the start-probability model needs to know they were available, and that
    comes from the roster, not from a stat line full of zeroes.
    """
    if goalie_ids is None:
        goalie_ids = known_goalie_ids(session)

    season = _season_from_game_id(game_id)
    intervals = load_goalie_shifts(session, game_id, goalie_ids)

    # Shots faced. `opponent_team_id` on a shot is the team being shot at,
    # so it identifies the goalie's team.
    shot_rows = session.execute(
        text(f"""
            SELECT goalie_id, opponent_team_id, event_type, is_goal,
                   strength_state
            FROM shot_attempts
            WHERE game_id = :gid AND period <= :maxp
                  AND event_type IN {FENWICK_EVENTS}
        """),
        {"gid": game_id, "maxp": MAX_SCORING_PERIOD},
    ).fetchall()

    rows: dict[int, GoalieGameRow] = {}
    team_goalies: dict[int, set[int]] = {home_team_id: set(), away_team_id: set()}
    empty_net_by_team: dict[int, int] = {home_team_id: 0, away_team_id: 0}

    def ensure_row(goalie_id: int, team_id: int) -> GoalieGameRow:
        if goalie_id not in rows:
            opponent = (
                away_team_id if team_id == home_team_id else home_team_id
            )
            rows[goalie_id] = GoalieGameRow(
                game_id=game_id,
                goalie_id=goalie_id,
                team_id=team_id,
                opponent_team_id=opponent,
                game_date=game_date,
                season=season,
                is_home=(team_id == home_team_id),
                team_score=(
                    home_score if team_id == home_team_id else away_score
                ),
                opponent_score=(
                    away_score if team_id == home_team_id else home_score
                ),
            )
            team_goalies.setdefault(team_id, set()).add(goalie_id)
        return rows[goalie_id]

    for goalie_id, defending_team, event_type, is_goal, strength in shot_rows:
        if goalie_id is None:
            # Empty net. Not charged to any goalie, tracked for the team so
            # the log reconciles against the official box score.
            if is_goal and defending_team in empty_net_by_team:
                empty_net_by_team[defending_team] += 1
            continue
        if defending_team is None:
            continue

        row = ensure_row(goalie_id, defending_team)

        if event_type in FENWICK_EVENTS:
            row.fenwick_against += 1
        if event_type not in ON_NET_EVENTS:
            continue

        row.shots_against += 1
        situation = _goalie_situation(strength)
        setattr(row, f"{situation}_shots_against",
                getattr(row, f"{situation}_shots_against") + 1)
        if is_goal:
            row.goals_against += 1
            setattr(row, f"{situation}_goals_against",
                    getattr(row, f"{situation}_goals_against") + 1)

    # Shifts can reveal a goalie who played but never faced a shot on net.
    for goalie_id, spans in intervals.items():
        if goalie_id in rows or not spans:
            continue
        team_id = session.execute(
            text("SELECT team_id FROM player_shifts "
                 "WHERE game_id = :gid AND player_id = :pid AND team_id IS NOT NULL "
                 "LIMIT 1"),
            {"gid": game_id, "pid": goalie_id},
        ).scalar()
        if team_id in (home_team_id, away_team_id):
            ensure_row(goalie_id, team_id)

    if not rows:
        return []

    # Appearance details from the shift record.
    for goalie_id, row in rows.items():
        spans = intervals.get(goalie_id, [])
        row.toi_seconds = sum(end - start for start, end in spans)
        row.is_start = any(start == 0 for start, _ in spans)
        row.is_relief = bool(spans) and not row.is_start
        row.saves = row.shots_against - row.goals_against
        row.empty_net_ga_team = empty_net_by_team.get(row.team_id, 0)

    # A goalie played the whole game if they were the only one to appear
    # for their team and they started.
    for team_id, goalies in team_goalies.items():
        appeared = [g for g in goalies if intervals.get(g)]
        if len(appeared) == 1:
            row = rows[appeared[0]]
            row.played_full_game = row.is_start

    if home_score is not None and away_score is not None:
        goals = load_goal_sequence(session, game_id)
        decisions = assign_decisions(
            goals, intervals, team_goalies,
            home_team_id, away_team_id, home_score, away_score,
            shootout=went_to_shootout(session, game_id),
        )
        for goalie_id, result in decisions.items():
            if goalie_id in rows:
                rows[goalie_id].decision = result

        # A shutout needs the goalie to have played the entire game and the
        # opposition to have finished with nothing. Using the final score
        # rather than the goalie's own goals-against handles both the
        # empty-net goal and the shootout loss, where the opponent is
        # credited a goal the goalie never faced.
        for row in rows.values():
            row.shutout = bool(row.played_full_game and row.opponent_score == 0)

    for row in rows.values():
        row.compute_fpts()

    return list(rows.values())

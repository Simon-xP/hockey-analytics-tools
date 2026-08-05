"""WeekState assembly — one immutable snapshot, one `as_of`.

Everything above this module reads from a `WeekState` and nothing else. That
is the whole point: a single object, built once, at a single knowledge cutoff,
so time leakage has one place to get in and one place to be tested.

The performance-critical piece is the projection cache. The planner builds
thousands of candidate grids; if each one called `forecast_player()` it would
take hours. So `build_week_state` resolves every projection it will ever need
up front, and after that the grid is pure arithmetic over a dict — no module
below the planner touches the database or `src/predict/`.

    state = build_week_state(session, league_key, my_team_key,
                             as_of, week_start, week_end)
    grid = build_grid(state, window_start, window_end)

Candidate pools are discovered later (P2 skaters, P3 goalies), so they top the
cache up with `resolve_projections`, which returns a new state rather than
mutating the old one.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Iterable, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.models import Player, Team, TeamRoster, YahooTransaction
from src.core.scoring import SKATER_WEIGHTS
from src.optimize.injuries import load_injuries
from src.optimize.models import RosterSlotSettings
from src.optimize.models.week import (
    LeagueSettings,
    ProjectionCache,
    RosterEntry,
    WeekState,
)

logger = logging.getLogger(__name__)

# NHL position codes to Yahoo eligibility codes. This mapping was duplicated
# in five files; this is the one definition. Everything else imports it.
NHL_TO_YAHOO_POSITION = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}

# How far past the planning window we project. A CONTEST window ends at
# `week_end` and needs seven terminal-value days after it; a PUNT window is
# next Monday through next Sunday and needs seven days after *that*. Covering
# `week_end + 14` means posture can slide the window without a second pass
# over the database.
TERMINAL_DAYS = 7
PROJECTION_HORIZON_DAYS = 14

# Yahoo statuses that let a player be parked in an IR slot. "OUT" is
# deliberately absent: Yahoo only permits the move on an IR designation, and
# treating a day-to-day scratch as IR-eligible would invent roster space we
# do not have.
IR_DESIGNATED_STATUSES = frozenset({"IR", "IR+", "IR-LT", "IR-NR"})

# Situation bonuses, mirroring `src.predict.forecasting.projections`. Earned
# points must be scored the same way projected points are, or the matchup gap
# is measured against a moving ruler.
PP_GOAL_BONUS = 1.3
PP_ASSIST_BONUS = 1.0
SH_GOAL_BONUS = 3.0
SH_ASSIST_BONUS = 2.0


# ---------------------------------------------------------------------------
# Forecast dependencies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastDeps:
    """The trained models `forecast_player` needs, loaded once.

    Deliberately *not* a module-level singleton. `src/optimize/value.py` has
    one (`_FORECAST_CACHE`) and it is not `as_of`-aware, so a backtest that
    runs after a live call gets handed the wrong models. Build these per run
    and pass them down.
    """

    models: dict
    toi_predictor: object
    eb_pp: object
    eb_pk: object
    eb_5v5: object


def load_forecast_deps() -> ForecastDeps:
    """Load the situation models, TOI predictor, and empirical Bayes priors."""
    from src.predict.forecasting.empirical_bayes import EmpiricalBayesPredictor
    from src.predict.forecasting.forecast import load_models
    from src.predict.forecasting.toi_model import TOIPredictor

    return ForecastDeps(
        models=load_models(),
        toi_predictor=TOIPredictor(),
        eb_pp=EmpiricalBayesPredictor("pp", ["goals", "assists", "shots"]),
        eb_pk=EmpiricalBayesPredictor("pk", ["goals", "assists"]),
        eb_5v5=EmpiricalBayesPredictor("5v5", ["goals", "assists"]),
    )


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


def build_schedule_map(
    session: Session,
    start: date,
    end: date,
) -> dict[date, frozenset[str]]:
    """`day -> team abbreviations playing that day`, for the whole range.

    One query. The old path (`value.py::get_teams_playing_on_date`) opened a
    fresh session per call, inside a loop over games, inside a loop over
    players, and queried `teams` twice per game. That was the single biggest
    reason the heavy path was slow.
    """
    rows = session.execute(
        text(
            """
            SELECT g.date, home.abbrev, away.abbrev
            FROM games g
            JOIN teams home ON home.team_id = g.home_team_id
            JOIN teams away ON away.team_id = g.away_team_id
            WHERE g.date >= :start AND g.date <= :end
            """
        ),
        {"start": start, "end": end},
    ).fetchall()

    by_day: dict[date, set[str]] = {}
    for game_date, home_abbrev, away_abbrev in rows:
        day = by_day.setdefault(game_date, set())
        day.add(home_abbrev)
        day.add(away_abbrev)
    return {day: frozenset(teams) for day, teams in by_day.items()}


def build_game_context(
    session: Session,
    start: date,
    end: date,
) -> dict[tuple[date, int], tuple[int, int]]:
    """`(day, team_id) -> (opponent_team_id, home_team_id)`.

    The forecast wants real opponent and venue context. Without it,
    `forecast_player` falls back to "whoever they played last", which is a
    proxy we do not need when the schedule is right there.
    """
    rows = session.execute(
        text(
            """
            SELECT date, home_team_id, away_team_id
            FROM games
            WHERE date >= :start AND date <= :end
            """
        ),
        {"start": start, "end": end},
    ).fetchall()

    context: dict[tuple[date, int], tuple[int, int]] = {}
    for game_date, home_id, away_id in rows:
        context[(game_date, home_id)] = (away_id, home_id)
        context[(game_date, away_id)] = (home_id, home_id)
    return context


def terminal_window(window_end: date, days: int = TERMINAL_DAYS) -> tuple[date, date]:
    """The `days`-long stretch immediately after a planning window closes."""
    return window_end + timedelta(days=1), window_end + timedelta(days=days)


def days_between(start: date, end: date) -> list[date]:
    """Every calendar day from `start` to `end`, inclusive."""
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


def yahoo_positions(player: Player) -> tuple[str, ...]:
    """Yahoo eligibility for a player, falling back to the NHL position."""
    if player.yahoo_positions:
        parsed = tuple(p.strip() for p in player.yahoo_positions.split(",") if p.strip())
        if parsed:
            return parsed
    nhl = (player.position or "C").strip()
    return (NHL_TO_YAHOO_POSITION.get(nhl, nhl),)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def expected_return_date(injury: Mapping, as_of: date) -> date | None:
    """Midpoint of the reported return window, or None for season-ending.

    None means "assume out indefinitely", which is the same convention
    `src.optimize.injuries.estimate_games_missed` uses.
    """
    soonest = injury.get("soonest_return")
    latest = injury.get("latest_return")
    if soonest and latest:
        return soonest + timedelta(days=(latest - soonest).days // 2)
    return soonest or latest


def is_available(
    injury: Mapping | None,
    day: date,
    as_of: date,
) -> bool:
    """Whether we expect this player to dress on `day`."""
    if not injury:
        return True
    expected_back = expected_return_date(injury, as_of)
    if expected_back is None:
        return False  # season-ending or no stated timeline
    return day >= expected_back


# ---------------------------------------------------------------------------
# Earned points
# ---------------------------------------------------------------------------


def compute_earned(
    session: Session,
    nhl_ids: Iterable[int],
    week_start: date,
    as_of: date,
) -> float:
    """Fantasy points already banked this week, strictly before `as_of`.

    Scored exactly the way `src.predict.forecasting.projections` scores a
    projection, so earned and projected points are the same currency.

    Note: this replaces `matchup/scoreboard.py::build_matchup_snapshot_from_db`,
    which reads a `GameIndividualStats.fpts` column that does not exist.
    """
    ids = list(nhl_ids)
    if not ids:
        return 0.0

    rows = session.execute(
        text(
            """
            SELECT gas.situation,
                   COALESCE(SUM(gas.goals), 0),
                   COALESCE(SUM(gas.assists), 0),
                   COALESCE(SUM(gas.shots), 0),
                   COALESCE(SUM(gas.hits), 0),
                   COALESCE(SUM(gas.blocks), 0)
            FROM game_advanced_stats gas
            JOIN games g ON gas.game_id = g.game_id
            WHERE gas.player_id = ANY(:ids)
                  AND g.date >= :week_start
                  AND g.date < :as_of
                  AND gas.situation IN ('all', 'pp', 'pk')
            GROUP BY gas.situation
            """
        ),
        {"ids": ids, "week_start": week_start, "as_of": as_of},
    ).fetchall()

    totals = {r[0]: r[1:] for r in rows}
    fpts = 0.0

    if "all" in totals:
        goals, assists, shots, hits, blocks = totals["all"]
        fpts += goals * SKATER_WEIGHTS["goals"]
        fpts += assists * SKATER_WEIGHTS["assists"]
        fpts += shots * SKATER_WEIGHTS["shots"]
        fpts += hits * SKATER_WEIGHTS["hits"]
        fpts += blocks * SKATER_WEIGHTS["blocks"]

    if "pp" in totals:
        fpts += totals["pp"][0] * PP_GOAL_BONUS + totals["pp"][1] * PP_ASSIST_BONUS
    if "pk" in totals:
        fpts += totals["pk"][0] * SH_GOAL_BONUS + totals["pk"][1] * SH_ASSIST_BONUS

    goalie_fpts = session.execute(
        text(
            """
            SELECT COALESCE(SUM(fpts), 0)
            FROM goalie_game_log
            WHERE goalie_id = ANY(:ids)
                  AND game_date >= :week_start
                  AND game_date < :as_of
            """
        ),
        {"ids": ids, "week_start": week_start, "as_of": as_of},
    ).scalar()

    return float(fpts) + float(goalie_fpts or 0.0)


def count_adds_used(
    session: Session,
    league_key: str,
    team_key: str,
    week_start: date,
    as_of: date,
) -> int:
    """Adds this team has already spent this week, strictly before `as_of`."""
    return int(
        session.query(YahooTransaction)
        .filter(
            YahooTransaction.league_key == league_key,
            YahooTransaction.fantasy_team_key == team_key,
            YahooTransaction.action == "add",
            YahooTransaction.timestamp >= week_start,
            YahooTransaction.timestamp < as_of,
        )
        .count()
    )


# ---------------------------------------------------------------------------
# League settings
# ---------------------------------------------------------------------------


def load_league_settings(
    league_key: str,
    slots: RosterSlotSettings | None = None,
) -> LeagueSettings:
    """Read league configuration from Yahoo, logging anything we default.

    The settings endpoint is unreachable all off-season. When it fails we fall
    back to `config.settings.LEAGUE_SETTINGS_FALLBACK` and say so at WARNING
    level for every field, because an assumed `adds_per_week` silently
    changing the shape of a plan is exactly the kind of bug that never gets
    found.
    """
    from config.settings import LEAGUE_SETTINGS_FALLBACK

    remote: dict = {}
    try:
        from src.ingest.yahoo.client import get_league_settings

        remote = get_league_settings(league_key) or {}
    except Exception as exc:  # network, auth, off-season — all the same to us
        logger.warning(
            "Yahoo league settings unavailable for %s (%s); using fallbacks",
            league_key, exc,
        )

    def pick(field: str) -> int:
        if field in remote:
            return int(remote[field])
        value = LEAGUE_SETTINGS_FALLBACK[field]
        logger.warning("League %s: %s not reported by Yahoo, defaulting to %s",
                       league_key, field, value)
        return int(value)

    if slots is None:
        slots = _slots_from_yahoo(remote.get("roster_positions")) or RosterSlotSettings()

    return LeagueSettings(
        league_key=league_key,
        slots=slots,
        n_teams=pick("n_teams"),
        adds_per_week=pick("adds_per_week"),
        waiver_days=pick("waiver_days"),
        roster_size=pick("roster_size"),
    )


def _slots_from_yahoo(positions: Mapping[str, int] | None) -> RosterSlotSettings | None:
    """Translate Yahoo's roster_positions into our slot settings."""
    if not positions:
        return None
    return RosterSlotSettings(
        c=positions.get("C", 0),
        lw=positions.get("LW", 0),
        rw=positions.get("RW", 0),
        d=positions.get("D", 0),
        g=positions.get("G", 0),
        util=positions.get("Util", positions.get("UTIL", 0)),
        bn=positions.get("BN", 0),
        ir=positions.get("IR", 0),
        ir_plus=positions.get("IR+", 0),
    )


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


def get_team_roster_nhl_ids(session: Session, league_key: str, team_key: str) -> list[int]:
    """NHL IDs for a team's current roster.

    Lives here rather than in `week/light.py` because state assembly is the
    thing that owns "who is on this roster"; `light.py` imports it.
    """
    rows = (
        session.query(TeamRoster.nhl_id)
        .filter(TeamRoster.league_key == league_key, TeamRoster.team_key == team_key)
        .all()
    )
    return [r.nhl_id for r in rows]


def build_roster_entries(
    session: Session,
    nhl_ids: Sequence[int],
    as_of: date,
    ir_capacity: int,
    protected: frozenset[int],
) -> tuple[tuple[RosterEntry, ...], int]:
    """Build `RosterEntry` rows and count how many IR slots are still open."""
    if not nhl_ids:
        return (), ir_capacity

    players = session.query(Player).filter(Player.nhl_id.in_(list(nhl_ids))).all()
    teams = {t.team_id: t.abbrev for t in session.query(Team).all()}
    injuries = load_injuries(session, set(nhl_ids), as_of)

    # IR designations are claimed in roster order until the slots run out; a
    # player who cannot fit is simply not IR-eligible right now.
    designated = [
        p.nhl_id
        for p in players
        if (injuries.get(p.nhl_id, {}).get("injury_status") or "").upper()
        in IR_DESIGNATED_STATUSES
    ]
    on_ir = set(designated[:ir_capacity])
    open_ir_spots = max(0, ir_capacity - len(on_ir))

    entries = []
    for player in players:
        injury = injuries.get(player.nhl_id)
        status = injury.get("injury_status") if injury else None
        entries.append(
            RosterEntry(
                nhl_id=player.nhl_id,
                name=player.full_name,
                team_abbrev=teams.get(player.team_id, ""),
                positions=yahoo_positions(player),
                injury_status=status,
                expected_return=expected_return_date(injury, as_of) if injury else None,
                ir_eligible=(
                    player.nhl_id in on_ir
                    or (
                        (status or "").upper() in IR_DESIGNATED_STATUSES
                        and open_ir_spots > 0
                    )
                ),
                is_protected=player.nhl_id in protected,
            )
        )

    entries.sort(key=lambda e: e.nhl_id)
    return tuple(entries), open_ir_spots


# ---------------------------------------------------------------------------
# Projection cache
# ---------------------------------------------------------------------------


def build_projection_cache(
    session: Session,
    nhl_ids: Iterable[int],
    days: Sequence[date],
    as_of: date,
    schedule: Mapping[date, frozenset[str]],
    game_context: Mapping[tuple[date, int], tuple[int, int]],
    deps: ForecastDeps,
    *,
    team_by_player: Mapping[int, tuple[int, str]],
    unavailable: Mapping[int, frozenset[date]] | None = None,
    existing: Mapping[tuple[int, date], float] | None = None,
) -> dict[tuple[int, date], float]:
    """Resolve `(player, day) -> expected FPTS` for everyone who plays.

    Absent keys mean "no game we can value": the team is idle, the player is
    expected to be out, or the forecast could not be produced. Never zero.

    Repeated `(player, opponent, venue)` triples are memoized, because a
    forecast at a fixed `as_of` varies only with game context — the feature
    extraction underneath is identical for every future date.
    """
    from src.predict.forecasting.forecast import forecast_player

    values: dict[tuple[int, date], float] = dict(existing or {})
    unavailable = unavailable or {}
    memo: dict[tuple[int, int, int], float] = {}
    failures = 0

    for nhl_id in nhl_ids:
        entry = team_by_player.get(nhl_id)
        if entry is None:
            continue
        team_id, team_abbrev = entry
        out_days = unavailable.get(nhl_id, frozenset())

        for day in days:
            if (nhl_id, day) in values:
                continue
            if team_abbrev not in schedule.get(day, frozenset()):
                continue
            if day in out_days:
                continue

            context = game_context.get((day, team_id))
            if context is None:
                continue
            opp_team_id, home_team_id = context

            memo_key = (nhl_id, opp_team_id, home_team_id)
            if memo_key in memo:
                values[(nhl_id, day)] = memo[memo_key]
                continue

            try:
                forecast = forecast_player(
                    session, nhl_id, day,
                    opp_team_id=opp_team_id,
                    home_team_id=home_team_id,
                    models=deps.models,
                    toi_predictor=deps.toi_predictor,
                    eb_pp=deps.eb_pp,
                    eb_pk=deps.eb_pk,
                    eb_5v5=deps.eb_5v5,
                    as_of=as_of,
                )
            except Exception:
                # No history, no model coverage — the player has no projection
                # and therefore no entry. Downstream must not guess a zero.
                failures += 1
                continue

            fpts = float(forecast.get("fpts", 0.0)) if forecast else None
            if fpts is None:
                failures += 1
                continue
            memo[memo_key] = fpts
            values[(nhl_id, day)] = fpts

    if failures:
        logger.info("Projection cache: %d player-games had no forecast", failures)
    return values


def _team_lookup(session: Session, nhl_ids: Iterable[int]) -> dict[int, tuple[int, str]]:
    """`nhl_id -> (team_id, team_abbrev)` for a set of players."""
    ids = list(nhl_ids)
    if not ids:
        return {}
    rows = (
        session.query(Player.nhl_id, Player.team_id, Team.abbrev)
        .join(Team, Team.team_id == Player.team_id)
        .filter(Player.nhl_id.in_(ids))
        .all()
    )
    return {nhl_id: (team_id, abbrev) for nhl_id, team_id, abbrev in rows}


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_week_state(
    session: Session,
    league_key: str,
    my_team_key: str,
    as_of: date,
    week_start: date,
    week_end: date,
    *,
    opp_team_key: str | None = None,
    candidate_nhl_ids: Iterable[int] | None = None,
    league: LeagueSettings | None = None,
    deps: ForecastDeps | None = None,
    projection_end: date | None = None,
) -> WeekState:
    """Assemble the immutable snapshot everything else reads from.

    Args:
        session: DB session.
        league_key: Yahoo league key.
        my_team_key: The team we are planning for.
        as_of: Knowledge cutoff. Nothing on or after this date is visible.
        week_start: Monday of the current fantasy week.
        week_end: Sunday of the current fantasy week, inclusive.
        opp_team_key: This week's opponent. Looked up from Yahoo when omitted;
            an empty string when Yahoo cannot be reached.
        candidate_nhl_ids: Extra players to pre-resolve projections for. P2
            and P3 discover their pools later and top the cache up with
            `resolve_projections`, so this is only for callers that already
            know who they care about.
        league: Pre-built league settings, to skip the Yahoo round trip.
        deps: Pre-loaded forecast models, to skip the model load.
        projection_end: Last day to project. Defaults to `week_end + 14`,
            which covers a PUNT window (next Mon-Sun) *and* its terminal
            seven days, so posture can slide the window without a second pass
            over the database.

    Returns:
        A `WeekState` whose projection cache is populated for the whole
        roster over the whole horizon.
    """
    from config.settings import PROTECTED_NHL_IDS

    if league is None:
        league = load_league_settings(league_key)
    if deps is None:
        deps = load_forecast_deps()
    if projection_end is None:
        projection_end = week_end + timedelta(days=PROJECTION_HORIZON_DAYS)

    horizon_start = min(as_of, week_start)
    horizon = days_between(horizon_start, projection_end)
    schedule = build_schedule_map(session, horizon_start, projection_end)
    game_context = build_game_context(session, horizon_start, projection_end)

    my_ids = get_team_roster_nhl_ids(session, league_key, my_team_key)

    if opp_team_key is None:
        opp_team_key = _lookup_opponent(league_key, my_team_key)
    opp_ids = (
        get_team_roster_nhl_ids(session, league_key, opp_team_key) if opp_team_key else []
    )

    ir_capacity = league.slots.ir + league.slots.ir_plus
    roster, open_ir_spots = build_roster_entries(
        session, my_ids, as_of, ir_capacity, frozenset(PROTECTED_NHL_IDS)
    )

    on_ir = sum(1 for e in roster if e.ir_eligible and e.injury_status)
    on_ir = min(on_ir, ir_capacity)
    open_active_spots = max(0, league.roster_size - (len(roster) - on_ir))

    injuries = load_injuries(session, {e.nhl_id for e in roster}, as_of)
    unavailable = {
        e.nhl_id: frozenset(
            day for day in horizon if not is_available(injuries.get(e.nhl_id), day, as_of)
        )
        for e in roster
    }

    cache_ids = list(my_ids)
    if candidate_nhl_ids:
        cache_ids += [n for n in candidate_nhl_ids if n not in set(my_ids)]

    values = build_projection_cache(
        session, cache_ids, horizon, as_of, schedule, game_context, deps,
        team_by_player=_team_lookup(session, cache_ids),
        unavailable=unavailable,
    )

    adds_used = count_adds_used(session, league_key, my_team_key, week_start, as_of)
    opp_adds_used = (
        count_adds_used(session, league_key, opp_team_key, week_start, as_of)
        if opp_team_key
        else 0
    )

    return WeekState(
        as_of=as_of,
        week_start=week_start,
        week_end=week_end,
        league=league,
        my_team_key=my_team_key,
        opp_team_key=opp_team_key or "",
        roster=roster,
        my_earned=compute_earned(session, my_ids, week_start, as_of),
        opp_earned=compute_earned(session, opp_ids, week_start, as_of),
        adds_remaining=max(0, league.adds_per_week - adds_used),
        opp_adds_remaining=max(0, league.adds_per_week - opp_adds_used),
        open_active_spots=open_active_spots,
        open_ir_spots=open_ir_spots,
        schedule=schedule,
        projections=ProjectionCache(values=values),
    )


def _lookup_opponent(league_key: str, my_team_key: str) -> str:
    """This week's opponent from Yahoo, or "" when Yahoo is unreachable."""
    try:
        from src.ingest.yahoo.client import get_matchup

        matchup = get_matchup(league_key) or {}
        for team in matchup.get("teams", []):
            if team.get("team_key") and team["team_key"] != my_team_key:
                return team["team_key"]
    except Exception as exc:
        logger.warning("Could not resolve opponent for %s (%s)", my_team_key, exc)
    return ""


def resolve_projections(
    session: Session,
    state: WeekState,
    nhl_ids: Iterable[int],
    *,
    deps: ForecastDeps | None = None,
    through: date | None = None,
) -> WeekState:
    """Top the projection cache up with a newly discovered candidate pool.

    Returns a new `WeekState`. P2 and P3 call this once each, after they know
    which free agents are worth looking at, so the planner still ends up with
    a single cache and a single database pass per pool.
    """
    ids = [n for n in dict.fromkeys(nhl_ids)]
    if not ids:
        return state

    if deps is None:
        deps = load_forecast_deps()
    if through is None:
        through = max(state.schedule) if state.schedule else state.week_end

    horizon = days_between(min(state.as_of, state.week_start), through)
    game_context = build_game_context(session, horizon[0], horizon[-1])

    values = build_projection_cache(
        session, ids, horizon, state.as_of, state.schedule, game_context, deps,
        team_by_player=_team_lookup(session, ids),
        existing=state.projections.values,
    )
    return replace(state, projections=ProjectionCache(values=values))


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def state_fingerprint(state: WeekState) -> str:
    """Stable hash of the facts a plan depends on.

    P7 compares this against the fingerprint stored on a `WeekPlan` to decide
    whether the plan is stale. It covers the inputs that would change the
    answer — roster, injuries, earned scores, add budget, window — and
    deliberately excludes the projection cache, which is derived from them.
    """
    parts = [
        state.as_of.isoformat(),
        state.week_start.isoformat(),
        state.week_end.isoformat(),
        state.my_team_key,
        state.opp_team_key,
        f"{state.my_earned:.2f}",
        f"{state.opp_earned:.2f}",
        str(state.adds_remaining),
        str(state.opp_adds_remaining),
        str(state.open_active_spots),
        str(state.open_ir_spots),
    ]
    for entry in sorted(state.roster, key=lambda e: e.nhl_id):
        parts.append(
            f"{entry.nhl_id}:{entry.injury_status or ''}:"
            f"{entry.expected_return or ''}:{int(entry.is_protected)}"
        )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

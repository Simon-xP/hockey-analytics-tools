"""Matchup state: both teams' projections in, a `Posture` out.

    posture = fetch_posture(session, league_key)          # live, hits Yahoo
    posture = determine_posture(ctx, as_of=today)         # pure, no I/O

`Posture` answers three things at once — whether we contest this week, which
seven days the planner should optimize, and how deep into our own roster we may
reach for a move. See `src/optimize/week/posture.py` for the policy and
`docs/plans/weekly-optimizer/06-posture.md` for why it is shaped this way.

The pieces underneath:

    scoreboard.py       where the matchup actually stands, live or reconstructed
    win_probability.py  both teams' (mu, sigma) -> P(win)
    state_engine.py     P(win) + importance -> Posture
"""

from datetime import date

from sqlalchemy.orm import Session

from src.optimize.matchup.scoreboard import fetch_matchup_snapshot
from src.optimize.matchup.state_engine import (
    auto_importance,
    determine_aggression,
    determine_posture,
)
from src.optimize.models import (
    AggressionLevel,
    MatchupContext,
    RosterSlotSettings,
    WeekImportance,
    WinProbability,
)
from src.optimize.models.week import Posture, PostureMode
from src.optimize.week.light import model_pickup_boost, project_team_remaining
from src.optimize.week.posture import WinValuation

__all__ = [
    "Posture",
    "PostureMode",
    "WinValuation",
    "auto_importance",
    "build_matchup_context",
    "determine_aggression_from_context",
    "determine_posture",
    "fetch_posture",
]


def determine_aggression_from_context(
    ctx: MatchupContext,
) -> tuple[AggressionLevel, WinProbability]:
    """Drop depth alone, for callers that do not need a window.

    Deprecated in favour of `determine_posture`, which returns the mode and the
    window alongside it. Kept because `src/backtest/` takes an
    `AggressionLevel` directly.
    """
    return determine_aggression(ctx)


def build_matchup_context(
    session: Session,
    league_key: str,
    as_of: date,
    yahoo_week: int | None = None,
    my_rank: int = 8,
    total_teams: int = 16,
    playoff_spots: int = 8,
    is_playoff: bool = False,
    importance: WeekImportance | None = None,
    weeks_remaining: int | None = None,
    roster_slot_settings: RosterSlotSettings | None = None,
) -> MatchupContext | None:
    """Fetch the scoreboard and project both sides of it.

    Returns `None` when Yahoo has no matchup for this week, which is a real
    state (off-season, All-Star break) and not an error.

    Expensive: `project_team_remaining` and `model_pickup_boost` each call
    `forecast_player` in a loop. Build this once per planning run and pass the
    result down. The opponent's distribution does not vary across our own
    candidate plans.
    """
    snapshot = fetch_matchup_snapshot(league_key, yahoo_week)
    if snapshot is None:
        return None

    projections = {}
    boosts = {}
    for side, team_key, adds in (
        ("mine", snapshot.my_team_key, snapshot.my_adds_remaining),
        ("opp", snapshot.opp_team_key, snapshot.opp_adds_remaining),
    ):
        earned = snapshot.my_earned if side == "mine" else snapshot.opp_earned
        projections[side] = project_team_remaining(
            session,
            league_key,
            team_key,
            as_of=as_of,
            week_end=snapshot.week_end,
            earned=earned,
            roster_slot_settings=roster_slot_settings,
        )
        boosts[side] = model_pickup_boost(
            session,
            league_key,
            team_key,
            adds_remaining=adds,
            as_of=as_of,
            week_end=snapshot.week_end,
            roster_slot_settings=roster_slot_settings,
        )

    if importance is None:
        importance = auto_importance(my_rank, playoff_spots, is_playoff, weeks_remaining)

    return MatchupContext(
        snapshot=snapshot,
        my_projection=projections["mine"],
        opp_projection=projections["opp"],
        my_pickup_boost=boosts["mine"],
        opp_pickup_boost=boosts["opp"],
        importance=importance,
        my_rank=my_rank,
        total_teams=total_teams,
        playoff_spots=playoff_spots,
        is_playoff=is_playoff,
    )


def fetch_posture(
    session: Session,
    league_key: str,
    as_of: date | None = None,
    yahoo_week: int | None = None,
    my_rank: int = 8,
    total_teams: int = 16,
    playoff_spots: int = 8,
    is_playoff: bool = False,
    importance: WeekImportance | None = None,
    weeks_remaining: int | None = None,
    roster_slot_settings: RosterSlotSettings | None = None,
    valuation: WinValuation | None = None,
) -> Posture:
    """Posture for the live matchup, straight from Yahoo.

    With no matchup available we contest a nominal week at NORMAL rather than
    guessing: an absent scoreboard is not evidence that the week is decided.
    """
    as_of = as_of or date.today()

    ctx = build_matchup_context(
        session,
        league_key,
        as_of,
        yahoo_week=yahoo_week,
        my_rank=my_rank,
        total_teams=total_teams,
        playoff_spots=playoff_spots,
        is_playoff=is_playoff,
        importance=importance,
        weeks_remaining=weeks_remaining,
        roster_slot_settings=roster_slot_settings,
    )

    if ctx is None:
        return Posture(
            mode=PostureMode.CONTEST,
            window_start=as_of,
            window_end=as_of,
            aggression=AggressionLevel.NORMAL,
            p_win=0.5,
            importance=importance or WeekImportance.BIG,
            reasoning=("No matchup found for this week — defaulting to normal play.",),
        )

    return determine_posture(ctx, as_of, valuation=valuation)

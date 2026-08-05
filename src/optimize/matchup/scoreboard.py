"""Fetch and parse Yahoo scoreboard into MatchupSnapshot."""

from datetime import date

from sqlalchemy.orm import Session

from src.core.models import Game, GameIndividualStats
from src.ingest.yahoo.client import get_scoreboard
from src.optimize.models import MatchupSnapshot


def _parse_day(raw: str | None) -> date:
    """Yahoo's `YYYY-MM-DD`, falling back to today when the field is absent."""
    return date.fromisoformat(raw) if raw else date.today()


def fetch_matchup_snapshot(
    league_key: str,
    week: int = None,
) -> MatchupSnapshot | None:
    """Build MatchupSnapshot from live Yahoo scoreboard data."""
    matchups = get_scoreboard(league_key, week)

    for matchup in matchups:
        teams = matchup["teams"]
        my_team = next((t for t in teams if t.get("is_owned_by_current_login")), None)
        if my_team is None:
            continue

        opp_team = next((t for t in teams if not t.get("is_owned_by_current_login")), None)
        if opp_team is None:
            continue

        return MatchupSnapshot(
            my_team_key=my_team["team_key"],
            opp_team_key=opp_team["team_key"],
            my_earned=my_team.get("points", 0.0),
            opp_earned=opp_team.get("points", 0.0),
            week_start=_parse_day(matchup.get("week_start")),
            week_end=_parse_day(matchup.get("week_end")),
            my_adds_remaining=4,
            opp_adds_remaining=4,
            yahoo_week=matchup.get("week", 0),
        )

    return None


def build_matchup_snapshot_from_db(
    session: Session,
    my_nhl_ids: set[int],
    opp_nhl_ids: set[int],
    my_team_key: str,
    opp_team_key: str,
    week_start: date,
    week_end: date,
    as_of: date,
    yahoo_week: int = 0,
    my_adds_remaining: int = 4,
    opp_adds_remaining: int = 4,
) -> MatchupSnapshot:
    """Reconstruct matchup scores from DB for backtesting.

    Sums FPTS from game stats for games played before as_of.
    """

    def _sum_earned(nhl_ids: set[int]) -> float:
        if not nhl_ids:
            return 0.0
        rows = (
            session.query(GameIndividualStats.fpts)
            .join(Game, GameIndividualStats.game_id == Game.game_id)
            .filter(
                GameIndividualStats.nhl_id.in_(nhl_ids),
                Game.date >= week_start,
                Game.date < as_of,
            )
            .all()
        )
        return sum(r.fpts for r in rows if r.fpts)

    return MatchupSnapshot(
        my_team_key=my_team_key,
        opp_team_key=opp_team_key,
        my_earned=_sum_earned(my_nhl_ids),
        opp_earned=_sum_earned(opp_nhl_ids),
        week_start=week_start,
        week_end=week_end,
        my_adds_remaining=my_adds_remaining,
        opp_adds_remaining=opp_adds_remaining,
        yahoo_week=yahoo_week,
    )

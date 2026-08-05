"""Light week optimization — opponents, and win-probability inputs for both teams.

Answers "how many points can this team realistically put up for the rest of
the week?" for *any* team in the league, by:

1. Projecting every rostered player's remaining games with the prediction
   module, assuming the manager sets an optimal daily lineup.
2. Greedily spending whatever add budget they have left on the
   highest-projected free agents.

Deliberately cheaper and simpler than `heavy`:

- No aggression weighting. An opponent's aggression is unknowable and
  modelling it adds variance without adding signal.
- No drop ranking or add/drop pair search. A pickup is assumed to displace
  a bench player, so its full projection counts as boost.
- Free agent candidates are capped (`fa_candidate_limit`) rather than
  exhaustively valued.

This is also what the matchup engine uses for *both* teams: `heavy` needs an
aggression level as an input, and aggression is derived from both teams'
projections, so `heavy` cannot be used to feed itself.
"""

from datetime import date

from sqlalchemy.orm import Session

from src.core.models import Game, Player, Team, TeamRoster
from src.optimize.models import (
    PickupBoost,
    RosterPlayer,
    RosterSlotSettings,
    TeamProjection,
)
from src.optimize.slots import assign_players_to_slots
from src.optimize.week.state import get_team_roster_nhl_ids
from src.optimize.week.variance import compute_team_sigma
from src.predict.forecasting.empirical_bayes import EmpiricalBayesPredictor
from src.predict.forecasting.forecast import forecast_player
from src.predict.forecasting.model import SituationModel
from src.predict.forecasting.toi_model import TOIPredictor


def get_free_agent_nhl_ids(session: Session, league_key: str) -> list[int]:
    """Get NHL IDs of players not on any team's roster in this league."""
    rostered = (
        session.query(TeamRoster.nhl_id)
        .filter(TeamRoster.league_key == league_key)
        .subquery()
    )
    rows = (
        session.query(Player.nhl_id)
        .filter(
            Player.yahoo_player_id.isnot(None),
            ~Player.nhl_id.in_(session.query(rostered.c.nhl_id)),
        )
        .all()
    )
    return [r.nhl_id for r in rows]


def get_remaining_game_dates(
    session: Session,
    team_abbrev: str,
    after_date: date,
    through_date: date,
) -> list[date]:
    """Game dates for a team between after_date (exclusive) and through_date (inclusive)."""
    rows = (
        session.query(Game.date)
        .join(Team, (Game.home_team_id == Team.team_id) | (Game.away_team_id == Team.team_id))
        .filter(
            Team.abbrev == team_abbrev,
            Game.date > after_date,
            Game.date <= through_date,
        )
        .distinct()
        .order_by(Game.date)
        .all()
    )
    return [r.date for r in rows]


def _build_roster_players(session: Session, nhl_ids: list[int]) -> list[RosterPlayer]:
    """Build RosterPlayer objects from NHL IDs."""
    players = session.query(Player).filter(Player.nhl_id.in_(nhl_ids)).all()
    roster_players = []
    for p in players:
        positions = p.yahoo_positions.split(",") if p.yahoo_positions else [p.position or "C"]
        roster_players.append(RosterPlayer(
            name=p.full_name,
            team=p.team.abbrev if p.team else "",
            positions=[pos.strip() for pos in positions],
            nhl_id=p.nhl_id,
        ))
    return roster_players


def project_team_remaining(
    session: Session,
    league_key: str,
    team_key: str,
    as_of: date,
    week_end: date,
    earned: float = 0.0,
    models: dict[str, SituationModel] | None = None,
    toi_predictor: TOIPredictor | None = None,
    eb_pp: EmpiricalBayesPredictor | None = None,
    eb_pk: EmpiricalBayesPredictor | None = None,
    eb_5v5: EmpiricalBayesPredictor | None = None,
    roster_slot_settings: RosterSlotSettings | None = None,
) -> TeamProjection:
    """Project remaining fantasy points for a team through week_end."""
    if roster_slot_settings is None:
        roster_slot_settings = RosterSlotSettings()

    nhl_ids = get_team_roster_nhl_ids(session, league_key, team_key)
    roster_players = _build_roster_players(session, nhl_ids)

    if not roster_players:
        return TeamProjection(
            team_key=team_key,
            earned=earned,
            mu_remaining=0.0,
            sigma_remaining=0.0,
            remaining_games=0,
            remaining_fillable_games=0,
            roster_nhl_ids=nhl_ids,
        )

    all_per_game_fpts = []
    total_games = 0
    fillable_games = 0

    remaining_dates = set()
    player_game_dates: dict[int, list[date]] = {}
    for rp in roster_players:
        if not rp.team:
            continue
        dates = get_remaining_game_dates(session, rp.team, as_of, week_end)
        if dates:
            player_game_dates[rp.nhl_id] = dates
            remaining_dates.update(dates)

    for game_date in sorted(remaining_dates):
        playing_today = [rp for rp in roster_players if game_date in player_game_dates.get(rp.nhl_id, [])]
        assignments = assign_players_to_slots(playing_today, roster_slot_settings)
        active_players = set()
        for pos, assigned in assignments.items():
            if pos not in ("BN",):
                for rp in assigned:
                    active_players.add(rp.nhl_id)

        for nhl_id in active_players:
            fcast = forecast_player(
                session, nhl_id, game_date,
                models=models, toi_predictor=toi_predictor,
                eb_pp=eb_pp, eb_pk=eb_pk, eb_5v5=eb_5v5,
                as_of=as_of,
            )
            fpts = fcast.get("fpts", 0.0) if fcast else 0.0
            all_per_game_fpts.append(fpts)
            fillable_games += 1

        total_games += len(playing_today)

    mu = sum(all_per_game_fpts)
    sigma = compute_team_sigma(all_per_game_fpts)

    return TeamProjection(
        team_key=team_key,
        earned=earned,
        mu_remaining=mu,
        sigma_remaining=sigma,
        remaining_games=total_games,
        remaining_fillable_games=fillable_games,
        roster_nhl_ids=nhl_ids,
    )


def _score_fa_for_dates(
    session: Session,
    nhl_id: int,
    game_dates: list[date],
    as_of: date,
    models=None,
    toi_predictor=None,
    eb_pp=None,
    eb_pk=None,
    eb_5v5=None,
) -> float:
    """Sum projected FPTS for a player across specific game dates."""
    total = 0.0
    for gd in game_dates:
        fcast = forecast_player(
            session, nhl_id, gd,
            models=models, toi_predictor=toi_predictor,
            eb_pp=eb_pp, eb_pk=eb_pk, eb_5v5=eb_5v5,
            as_of=as_of,
        )
        if fcast:
            total += fcast.get("fpts", 0.0)
    return total


def model_pickup_boost(
    session: Session,
    league_key: str,
    team_key: str,
    adds_remaining: int,
    as_of: date,
    week_end: date,
    roster_slot_settings: RosterSlotSettings | None = None,
    models: dict[str, SituationModel] | None = None,
    toi_predictor: TOIPredictor | None = None,
    eb_pp: EmpiricalBayesPredictor | None = None,
    eb_pk: EmpiricalBayesPredictor | None = None,
    eb_5v5: EmpiricalBayesPredictor | None = None,
    fa_candidate_limit: int = 40,
) -> PickupBoost:
    """Model the best add path a team could take with its remaining budget.

    Finds the free agents with the highest projected points over the rest of
    the week and assumes the team takes the top `adds_remaining` of them.
    """
    if adds_remaining <= 0:
        return PickupBoost(mu_boost=0.0, sigma_boost=0.0, n_adds_remaining=0)

    if roster_slot_settings is None:
        roster_slot_settings = RosterSlotSettings()

    fa_nhl_ids = get_free_agent_nhl_ids(session, league_key)
    if not fa_nhl_ids:
        return PickupBoost(mu_boost=0.0, sigma_boost=0.0, n_adds_remaining=adds_remaining)

    fa_players = (
        session.query(Player)
        .filter(Player.nhl_id.in_(fa_nhl_ids))
        .all()
    )

    fa_with_games = []
    for p in fa_players:
        team_abbrev = p.team.abbrev if p.team else None
        if not team_abbrev:
            continue
        game_dates = get_remaining_game_dates(session, team_abbrev, as_of, week_end)
        if not game_dates:
            continue
        positions = p.yahoo_positions.split(",") if p.yahoo_positions else [p.position or "C"]
        fa_with_games.append({
            "nhl_id": p.nhl_id,
            "name": p.name,
            "team": team_abbrev,
            "positions": [pos.strip() for pos in positions],
            "game_dates": game_dates,
        })

    if not fa_with_games:
        return PickupBoost(mu_boost=0.0, sigma_boost=0.0, n_adds_remaining=adds_remaining)

    scored = []
    for fa in fa_with_games[:fa_candidate_limit]:
        total_fpts = _score_fa_for_dates(
            session, fa["nhl_id"], fa["game_dates"], as_of,
            models=models, toi_predictor=toi_predictor,
            eb_pp=eb_pp, eb_pk=eb_pk, eb_5v5=eb_5v5,
        )
        scored.append({**fa, "projected_fpts": total_fpts})

    scored.sort(key=lambda x: x["projected_fpts"], reverse=True)

    selected = scored[:adds_remaining]
    per_game_fpts = []
    top_targets = []
    for pick in selected:
        fpts_per_game = pick["projected_fpts"] / len(pick["game_dates"]) if pick["game_dates"] else 0
        for _ in pick["game_dates"]:
            per_game_fpts.append(fpts_per_game)
        top_targets.append({
            "nhl_id": pick["nhl_id"],
            "name": pick["name"],
            "projected_fpts": pick["projected_fpts"],
        })

    mu_boost = sum(pick["projected_fpts"] for pick in selected)
    sigma_boost = compute_team_sigma(per_game_fpts)

    return PickupBoost(
        mu_boost=mu_boost,
        sigma_boost=sigma_boost,
        n_adds_remaining=adds_remaining,
        top_targets=top_targets,
    )


def optimize_week_light(
    session: Session,
    league_key: str,
    team_key: str,
    as_of: date,
    week_end: date,
    earned: float = 0.0,
    adds_remaining: int = 4,
    roster_slot_settings: RosterSlotSettings | None = None,
    models: dict[str, SituationModel] | None = None,
    toi_predictor: TOIPredictor | None = None,
    eb_pp: EmpiricalBayesPredictor | None = None,
    eb_pk: EmpiricalBayesPredictor | None = None,
    eb_5v5: EmpiricalBayesPredictor | None = None,
    fa_candidate_limit: int = 40,
):
    """Full light pass for one team: roster projection plus best add path.

    Returns a `TeamWeekResult` with no `plan` — the light path models what a
    team *could* score, not a set of transactions to execute.
    """
    from src.optimize.models import TeamWeekResult

    forecast_kwargs = dict(
        models=models, toi_predictor=toi_predictor,
        eb_pp=eb_pp, eb_pk=eb_pk, eb_5v5=eb_5v5,
    )

    projection = project_team_remaining(
        session, league_key, team_key,
        as_of=as_of, week_end=week_end, earned=earned,
        roster_slot_settings=roster_slot_settings,
        **forecast_kwargs,
    )

    boost = model_pickup_boost(
        session, league_key, team_key,
        adds_remaining=adds_remaining,
        as_of=as_of, week_end=week_end,
        roster_slot_settings=roster_slot_settings,
        fa_candidate_limit=fa_candidate_limit,
        **forecast_kwargs,
    )

    return TeamWeekResult(
        team_key=team_key,
        depth="light",
        projection=projection,
        pickup_boost=boost,
        plan=None,
    )

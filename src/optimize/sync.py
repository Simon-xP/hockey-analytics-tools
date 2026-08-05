"""Valuation sync -- materialize ML forecasts into the player_valuations table.

After each game day, run this to recompute forecasts for all relevant
players so the transaction system reads pre-computed values instead of
running the forecast model on demand.

Public API:
    sync_roster_players()       - sync all players on your Yahoo roster
    sync_free_agents()          - sync top N free agents
    sync_transaction_trends()   - sync players with rising/falling ownership
    sync_player()               - sync a single player by nhl_id

    sync_nightly()              - runs roster + free agents + trends in one call

    # TODO: sync_news_players() - resync players mentioned in recent news/injuries.
    # Would pull from the player_injuries table for recent entries and
    # resync those players plus their teammates (opportunity scores shift).
    # Wire into sync_nightly() as the final step once Daily Faceoff
    # scraping is running reliably.
"""

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.core.models import Game, Player, PlayerInjury, PlayerValuation, Team
from src.core.queries.stats_helpers import compute_fpts_per_gp
from src.core.resolver import resolve_player
from src.predict.forecasting.empirical_bayes import EmpiricalBayesPredictor
from src.predict.forecasting.forecast import forecast_player, load_models
from src.predict.forecasting.toi_model import TOIPredictor
from src.predict.signals.opportunity import compute_opportunity_score
from src.predict.signals.upside import compute_upside_score

SEVERITY_MISS_DAYS = {
    "day-to-day": 3,
    "week-to-week": 14,
    "month-plus": 45,
    "season": 365,
}

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sync_roster_players(
    session: Session,
    league_key: str,
    from_date: Optional[date] = None,
    season: str = "20252026",
    weeks_ahead: int = 3,
) -> list[PlayerValuation]:
    """Sync valuations for all players on your Yahoo roster."""
    from src.optimize.value import load_roster_from_yahoo

    roster = load_roster_from_yahoo(league_key, session)
    nhl_ids = [p.nhl_id for p in roster.players if p.nhl_id is not None]
    log.info("Syncing %d roster players", len(nhl_ids))
    return _sync_batch(session, nhl_ids, from_date, season, weeks_ahead)


def sync_free_agents(
    session: Session,
    league_key: str,
    count: int = 50,
    from_date: Optional[date] = None,
    season: str = "20252026",
    weeks_ahead: int = 3,
) -> list[PlayerValuation]:
    """Sync valuations for the top N free agents."""
    from src.ingest.yahoo.client import get_free_agents

    fa_players = get_free_agents(league_key, count=count)
    nhl_ids = _resolve_yahoo_players(session, fa_players)
    log.info("Syncing %d free agents", len(nhl_ids))
    return _sync_batch(session, nhl_ids, from_date, season, weeks_ahead)


def sync_transaction_trends(
    session: Session,
    league_key: str,
    count: int = 25,
    from_date: Optional[date] = None,
    season: str = "20252026",
    weeks_ahead: int = 3,
) -> list[PlayerValuation]:
    """Sync valuations for trending players (rising and falling ownership)."""
    from src.ingest.yahoo.client import get_trending_players

    trending = get_trending_players(league_key, count=count)
    nhl_ids = _resolve_yahoo_players(session, trending)
    log.info("Syncing %d trending players", len(nhl_ids))
    return _sync_batch(session, nhl_ids, from_date, season, weeks_ahead)


def sync_player(
    session: Session,
    nhl_id: int,
    forecast_deps: Optional[dict] = None,
    from_date: Optional[date] = None,
    season: str = "20252026",
    weeks_ahead: int = 3,
) -> Optional[PlayerValuation]:
    """Compute and store a full valuation for one player.

    Runs the ML forecast for each upcoming game and persists
    the results. Returns the upserted PlayerValuation row.
    """
    if from_date is None:
        from_date = date.today()

    if not Path("models/forecasting_v2").exists():
        raise RuntimeError(
            "Forecasting models not trained. Run: python -m scripts.train_xg_model"
        )

    if forecast_deps is None:
        forecast_deps = _load_forecast_deps()

    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player:
        log.warning("Player %d not found, skipping", nhl_id)
        return None

    if player.yahoo_positions:
        positions = [p.strip() for p in player.yahoo_positions.split(",")]
    else:
        nhl_to_yahoo = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}
        positions = [nhl_to_yahoo.get(player.position, player.position)]

    if "G" in positions:
        log.debug("Skipping goalie %s", player.full_name)
        return None

    fpts_data = compute_fpts_per_gp(session, nhl_id, season, as_of=from_date)
    avg_toi = fpts_data["avg_toi"] if fpts_data else 12.0
    games_played = fpts_data["gp"] if fpts_data else 0

    expected_return = _get_expected_return(session, nhl_id, from_date)

    all_games = _get_upcoming_games(session, nhl_id, from_date, weeks_ahead)
    if not all_games:
        log.debug("No upcoming games for %s", player.full_name)

    game_forecasts = []
    fpts_total = 0.0

    for game in all_games:
        if expected_return and game.date < expected_return:
            game_forecasts.append({
                "game_id": game.game_id,
                "date": str(game.date),
                "fpts": 0.0,
                "opp_team_id": (
                    game.away_team_id
                    if game.home_team_id == player.team_id
                    else game.home_team_id
                ),
                "home_team_id": game.home_team_id,
                "injured": True,
            })
            continue

        opp_team_id = (
            game.away_team_id
            if game.home_team_id == player.team_id
            else game.home_team_id
        )

        try:
            proj = forecast_player(
                session,
                nhl_id,
                game.date,
                opp_team_id=opp_team_id,
                home_team_id=game.home_team_id,
                as_of=from_date,
                **forecast_deps,
            )
            fpts = proj.get("fpts", 0.0)
        except Exception:
            log.warning(
                "Forecast failed for %s on %s, falling back to historical avg",
                player.full_name,
                game.date,
            )
            fpts = fpts_data["fpts_per_gp"] if fpts_data else 0.0

        game_forecasts.append({
            "game_id": game.game_id,
            "date": str(game.date),
            "fpts": round(fpts, 3),
            "opp_team_id": opp_team_id,
            "home_team_id": game.home_team_id,
        })
        fpts_total += fpts

    playable_games = [f for f in game_forecasts if not f.get("injured")]
    fpts_per_game = fpts_total / len(playable_games) if playable_games else (
        fpts_data["fpts_per_gp"] if fpts_data else 0.0
    )

    upside = compute_upside_score(session, nhl_id, as_of=from_date)
    opportunity = compute_opportunity_score(session, nhl_id, as_of=from_date)

    valuation = session.get(PlayerValuation, nhl_id)
    if valuation:
        valuation.fpts_per_game = round(fpts_per_game, 3)
        valuation.avg_toi = round(avg_toi, 1)
        valuation.games_played = games_played
        valuation.upside_score = round(upside, 3)
        valuation.opportunity_score = round(opportunity, 3)
        valuation.game_forecasts = game_forecasts
    else:
        valuation = PlayerValuation(
            nhl_id=nhl_id,
            fpts_per_game=round(fpts_per_game, 3),
            avg_toi=round(avg_toi, 1),
            games_played=games_played,
            upside_score=round(upside, 3),
            opportunity_score=round(opportunity, 3),
            game_forecasts=game_forecasts,
        )
        session.add(valuation)

    return valuation


def sync_nightly(
    session: Session,
    league_key: str,
    from_date: Optional[date] = None,
    season: str = "20252026",
    weeks_ahead: int = 3,
    fa_count: int = 50,
    trending_count: int = 25,
) -> dict[str, int]:
    """Nightly sync: roster + free agents + transaction trends.

    Deduplicates across sources so players on multiple lists are
    only forecasted once. Returns counts per source.
    """
    from src.optimize.value import load_roster_from_yahoo
    from src.ingest.yahoo.client import get_free_agents, get_trending_players

    if from_date is None:
        from_date = date.today()

    roster = load_roster_from_yahoo(league_key, session)
    roster_ids = {p.nhl_id for p in roster.players if p.nhl_id is not None}

    fa_ids = set()
    try:
        fa_players = get_free_agents(league_key, count=fa_count)
        fa_ids = set(_resolve_yahoo_players(session, fa_players))
    except Exception:
        log.warning("Could not fetch free agents")

    trending_ids = set()
    try:
        trending = get_trending_players(league_key, count=trending_count)
        trending_ids = set(_resolve_yahoo_players(session, trending))
    except Exception:
        log.warning("Could not fetch trending players")

    all_ids = sorted(roster_ids | fa_ids | trending_ids)
    log.info(
        "Nightly sync: %d roster, %d FA, %d trending -> %d unique players",
        len(roster_ids), len(fa_ids), len(trending_ids), len(all_ids),
    )

    results = _sync_batch(session, all_ids, from_date, season, weeks_ahead)

    return {
        "roster": len(roster_ids),
        "free_agents": len(fa_ids),
        "trending": len(trending_ids),
        "total_unique": len(all_ids),
        "synced": len(results),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_forecast_deps():
    models = load_models()
    toi = TOIPredictor()
    eb_pp = EmpiricalBayesPredictor("pp", ["goals", "assists", "shots"])
    eb_pk = EmpiricalBayesPredictor("pk", ["goals", "assists"])
    eb_5v5 = EmpiricalBayesPredictor("5v5", ["goals", "assists"])
    return {
        "models": models,
        "toi_predictor": toi,
        "eb_pp": eb_pp,
        "eb_pk": eb_pk,
        "eb_5v5": eb_5v5,
    }


def _resolve_yahoo_players(
    session: Session,
    yahoo_players: list[dict],
) -> list[int]:
    """Resolve a list of Yahoo player dicts to NHL IDs."""
    nhl_ids = []
    for p in yahoo_players:
        try:
            nhl_id = resolve_player(
                session, name=p.get("name"), team_abbrev=p.get("team")
            )
            if nhl_id:
                nhl_ids.append(nhl_id)
        except Exception:
            pass
    return nhl_ids


def _sync_batch(
    session: Session,
    nhl_ids: list[int],
    from_date: Optional[date] = None,
    season: str = "20252026",
    weeks_ahead: int = 3,
) -> list[PlayerValuation]:
    """Sync valuations for a batch of players.

    Loads forecast models once and reuses across all players.
    Commits after the full batch.
    """
    if from_date is None:
        from_date = date.today()

    deps = _load_forecast_deps()
    results = []

    for i, nhl_id in enumerate(nhl_ids):
        log.info(
            "Syncing %d/%d: player %d",
            i + 1, len(nhl_ids), nhl_id,
        )
        val = sync_player(
            session,
            nhl_id,
            forecast_deps=deps,
            from_date=from_date,
            season=season,
            weeks_ahead=weeks_ahead,
        )
        if val:
            results.append(val)

    session.commit()
    log.info("Synced %d player valuations", len(results))
    return results


def _get_expected_return(
    session: Session,
    nhl_id: int,
    as_of: date,
) -> Optional[date]:
    """Get the date a player is expected to return from injury.

    Returns None if the player is healthy (no active injury).
    Returns a date if injured -- either the explicit expected_return
    from the injury record, or an estimate based on severity.
    """
    latest = (
        session.query(PlayerInjury)
        .filter(PlayerInjury.nhl_id == nhl_id)
        .order_by(PlayerInjury.news_date.desc())
        .first()
    )

    if not latest:
        return None

    if latest.category in ("return", "transaction"):
        return None

    if latest.category != "injury":
        return None

    if latest.expected_return:
        if latest.expected_return <= as_of:
            return None
        return latest.expected_return

    miss_days = SEVERITY_MISS_DAYS.get(latest.severity or "unknown")
    if miss_days is None:
        return None

    news_date = latest.news_date.date() if latest.news_date else as_of
    estimated_return = news_date + timedelta(days=miss_days)

    if estimated_return <= as_of:
        return None

    log.debug(
        "Player %d injured (%s), estimated return %s",
        nhl_id, latest.severity, estimated_return,
    )
    return estimated_return


def _get_upcoming_games(
    session: Session,
    nhl_id: int,
    from_date: date,
    weeks_ahead: int = 3,
) -> list[Game]:
    """Get a player's upcoming games within the lookahead window."""
    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player or not player.team_id:
        return []

    end_date = from_date + timedelta(weeks=weeks_ahead)

    return (
        session.query(Game)
        .filter(
            Game.date >= from_date,
            Game.date <= end_date,
            or_(
                Game.home_team_id == player.team_id,
                Game.away_team_id == player.team_id,
            ),
        )
        .order_by(Game.date)
        .all()
    )

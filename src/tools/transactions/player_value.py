"""Player valuation for transaction decisions.

The key function is `compute_player_value()` which answers:
"How many fantasy points would this player contribute this week
if they were on my roster?" — accounting for slot availability.

This is NOT fpts_per_game * games. It only counts games where the
player can make the active lineup (not bench-blocked).
"""

from datetime import date, timedelta
from typing import Callable, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.core.models import Game, Player, Team
from src.core.resolver import resolve_player
from src.api.stats_helpers import compute_fpts_per_gp
from src.ingest.yahoo.client import get_my_team
from src.tools.schedule.models import Roster, RosterPlayer, RosterSlotSettings
from src.tools.schedule.optimizer import (
    assign_players_to_slots,
    get_teams_playing_on_date,
)
from src.tools.transactions.models import PlayerType, PlayerValue


def load_roster_from_yahoo(
    league_key: str,
    session: Session,
) -> Roster:
    """Build a Roster from the live Yahoo API.

    Fetches the authenticated user's team, resolves player names to
    NHL IDs, and returns a Roster object compatible with the schedule
    optimizer and transaction evaluator.

    Skips players with status NA (IR/inactive not on active roster).
    """
    team = get_my_team(league_key)
    players = []

    for p in team.get("roster", []):
        # Skip IR/inactive
        if p.get("selected_position") == "NA":
            continue

        name = p.get("name")
        team_abbrev = p.get("team")
        position_str = p.get("position", "")
        positions = [pos.strip() for pos in position_str.split(",")]

        # Resolve NHL ID
        nhl_id = None
        if name:
            try:
                nhl_id = resolve_player(session, name=name, team_abbrev=team_abbrev)
            except Exception:
                pass

        players.append(
            RosterPlayer(
                name=name or "",
                team=team_abbrev or "",
                positions=positions,
                nhl_id=nhl_id,
            )
        )

    return Roster(
        players=players,
        roster_slot_settings=RosterSlotSettings(),  # uses league defaults
    )


def get_team_week_games(
    session: Session,
    team_abbrev: str,
    yahoo_week: int,
) -> list[Game]:
    """Get all games for a team in a Yahoo fantasy week."""
    team = session.query(Team).filter(Team.abbrev == team_abbrev).first()
    if not team:
        return []

    games = (
        session.query(Game)
        .filter(
            Game.yahoo_week == yahoo_week,
            or_(
                Game.home_team_id == team.team_id,
                Game.away_team_id == team.team_id,
            ),
        )
        .order_by(Game.date)
        .all()
    )
    return games


def get_team_games_in_window(
    session: Session,
    team_abbrev: str,
    start_date: date,
    end_date: date,
) -> list[Game]:
    """Get all games for a team in a date window (inclusive)."""
    team = session.query(Team).filter(Team.abbrev == team_abbrev).first()
    if not team:
        return []

    games = (
        session.query(Game)
        .filter(
            Game.date >= start_date,
            Game.date <= end_date,
            or_(
                Game.home_team_id == team.team_id,
                Game.away_team_id == team.team_id,
            ),
        )
        .order_by(Game.date)
        .all()
    )
    return games


def get_team_remaining_games(
    session: Session,
    team_abbrev: str,
    from_date: Optional[date] = None,
) -> int:
    """Count remaining regular season games for a team from a given date."""
    if from_date is None:
        from_date = date.today()

    team = session.query(Team).filter(Team.abbrev == team_abbrev).first()
    if not team:
        return 0

    count = (
        session.query(Game)
        .filter(
            Game.date >= from_date,
            or_(
                Game.home_team_id == team.team_id,
                Game.away_team_id == team.team_id,
            ),
        )
        .count()
    )
    return count


def _default_forecast_fn(
    nhl_id: int,
    game_date: date,
    avg_toi: float,
) -> float:
    """Default forecast using the v2 situation-split model.

    Runs the full pipeline: extract features → predict per-60 rates
    by situation (5v5/PP/PK/Other) → predict TOI → combine into
    per-game FPTS with PP/SH bonuses.

    Returns projected fantasy points for one game.
    """
    from pathlib import Path
    from src.core.db import get_session
    from src.core.models import Game, Player
    from src.tools.forecasting.v2.model import SituationModel
    from src.tools.forecasting.v2.toi_model import TOIPredictor
    from src.tools.forecasting.v2.empirical_bayes import EmpiricalBayesPredictor
    from src.tools.forecasting.v2.features import extract_all_features
    from src.tools.forecasting.v2.projections import project_per_game
    from src.tools.forecasting.v2.constants import SITUATION_CONFIGS

    model_dir = Path("models/forecasting_v2")
    if not model_dir.exists():
        # No trained model — fall back to historical average
        data = compute_fpts_per_gp.__wrapped__(nhl_id) if hasattr(compute_fpts_per_gp, '__wrapped__') else None
        return avg_toi * 0.15  # rough fallback

    try:
        with get_session() as session:
            player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
            if not player:
                return 0.0

            team_id = player.team_id
            position = player.position or "C"

            # Find the game on this date
            game = (
                session.query(Game)
                .filter(
                    Game.date == game_date,
                    or_(
                        Game.home_team_id == team_id,
                        Game.away_team_id == team_id,
                    ),
                )
                .first()
            )
            if not game:
                return 0.0

            home_team_id = game.home_team_id
            opp_team_id = (
                game.away_team_id if team_id == game.home_team_id
                else game.home_team_id
            )

            current_year = game_date.year if game_date.month >= 9 else game_date.year - 1

            # Load situation models
            models = {}
            for sit in ["5v5", "pp", "pk"]:
                path = model_dir / f"{sit}_model.pkl"
                if path.exists():
                    models[sit] = SituationModel.load(path)

            toi_predictor = TOIPredictor()
            eb_pk = EmpiricalBayesPredictor("pk", ["goals", "assists"])

            predicted_rates = {}
            predicted_toi = {}

            for situation in SITUATION_CONFIGS:
                toi = toi_predictor.predict(
                    session, nhl_id, situation, game_date, current_year,
                )
                predicted_toi[situation] = toi

                if situation == "other":
                    predicted_rates[situation] = {
                        "goals_per60": 2.38,
                        "assists_per60": 3.06,
                    }
                elif situation == "pk":
                    eb_rates = eb_pk.predict(session, nhl_id, game_date)
                    rates = {
                        "goals_per60": eb_rates.get("goals_per60", 0),
                        "assists_per60": eb_rates.get("assists_per60", 0),
                    }
                    if "pk" in models:
                        features = extract_all_features(
                            session, nhl_id, "pk", game_date,
                            team_id, opp_team_id, home_team_id,
                            position, current_year,
                        )
                        poisson_rates = models["pk"].predict(features, toi_seconds=toi)
                        rates.update(poisson_rates)
                    predicted_rates[situation] = rates
                elif situation in models:
                    features = extract_all_features(
                        session, nhl_id, situation, game_date,
                        team_id, opp_team_id, home_team_id,
                        position, current_year,
                    )
                    predicted_rates[situation] = models[situation].predict(features)

            projected = project_per_game(predicted_rates, predicted_toi)
            return projected.get("fpts", 0.0)

    except Exception:
        return 0.0


def can_player_fill_slot(
    player: RosterPlayer,
    playing_players: list[RosterPlayer],
    roster_settings: RosterSlotSettings,
) -> bool:
    """Check if a player can fill an active roster slot on a given day.

    Adds the player to the day's playing list, runs slot assignment,
    and checks if the player was assigned to an active slot (not benched).
    """
    # If the player is already in the playing list, use as-is
    all_playing = list(playing_players)
    if not any(p.nhl_id == player.nhl_id for p in all_playing):
        all_playing.append(player)

    assignments = assign_players_to_slots(all_playing, roster_settings)

    # Check if the player was assigned to any active slot
    for pos, assigned in assignments.items():
        for p in assigned:
            if p.nhl_id == player.nhl_id:
                return True

    return False


def compute_player_value(
    session: Session,
    nhl_id: int,
    roster: Roster,
    yahoo_week: int,
    season: str = "20252026",
    forecast_fn: Optional[Callable[[int, date, float], float]] = None,
    as_of: Optional[date] = None,
) -> Optional[PlayerValue]:
    """Compute the full valuation of a player for a fantasy week.

    This is the core function of the transaction evaluator. It:
    1. Gets the player's team schedule for the week
    2. For each game day, checks if the player can fill an active slot
    3. Runs the forecast for each fillable game
    4. Sums to get weekly FPTS (slot-aware)

    Args:
        session: DB session
        nhl_id: NHL player ID
        roster: Current fantasy roster (the player may or may not be on it)
        yahoo_week: Yahoo fantasy week number
        season: Season string for stats lookup
        forecast_fn: Optional override for the forecast function.
            Signature: (nhl_id, game_date, avg_toi) -> projected_fpts.
            Defaults to the XGBoost forecast pipeline.

    Returns:
        PlayerValue with slot-aware weekly projection, or None if player
        not found.
    """
    if forecast_fn is None:
        forecast_fn = _default_forecast_fn

    # Look up player
    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player:
        return None

    team = session.query(Team).filter(Team.team_id == player.team_id).first()
    if not team:
        return None

    # Determine positions (prefer Yahoo positions, fall back to NHL position)
    if player.yahoo_positions:
        positions = [p.strip() for p in player.yahoo_positions.split(",")]
    else:
        # Map NHL position codes to Yahoo position codes
        nhl_to_yahoo = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}
        positions = [nhl_to_yahoo.get(player.position, player.position)]

    player_type = PlayerType.GOALIE if "G" in positions else PlayerType.SKATER

    # Goalies cannot be valued through the skater forecast pipeline.
    # Use goalie_eval.goalie_stream_to_player_value() instead.
    if player_type == PlayerType.GOALIE:
        return None

    # Build a RosterPlayer for slot checking
    roster_player = RosterPlayer(
        name=player.full_name,
        team=team.abbrev,
        positions=positions,
        nhl_id=nhl_id,
    )

    # Get team's games this week
    week_games = get_team_week_games(session, team.abbrev, yahoo_week)
    games_in_window = len(week_games)

    # Get avg TOI from historical data
    fpts_data = compute_fpts_per_gp(session, nhl_id, season, as_of=as_of)
    avg_toi = fpts_data["avg_toi"] if fpts_data else 12.0  # fallback for unknowns
    games_played = fpts_data["gp"] if fpts_data else 0

    # Check if the player is already on the roster
    is_on_roster = any(p.nhl_id == nhl_id for p in roster.players)

    # Build a working roster that includes this player (for slot checking)
    if is_on_roster:
        working_roster = roster
    else:
        working_roster = Roster(
            players=roster.players + [roster_player],
            roster_slot_settings=roster.roster_slot_settings,
        )

    # For each game day, check slot availability and project FPTS
    game_projections: dict[date, float] = {}
    fillable_games = 0

    for game in week_games:
        game_date = game.date

        # Get all roster players whose teams play this day
        teams_playing = get_teams_playing_on_date(game_date)
        playing_players = [
            p for p in working_roster.players if p.team in teams_playing
        ]

        # Check if this player can fill an active slot
        if can_player_fill_slot(
            roster_player, playing_players, roster.roster_slot_settings
        ):
            # Project FPTS for this game
            projected = forecast_fn(nhl_id, game_date, avg_toi)
            game_projections[game_date] = projected
            fillable_games += 1

    # Compute window FPTS
    window_fpts = sum(game_projections.values())
    fpts_per_game = window_fpts / fillable_games if fillable_games > 0 else 0.0

    # ROS value: fpts_per_game * remaining games for the team
    remaining_games = get_team_remaining_games(session, team.abbrev, from_date=as_of)
    ros_value = fpts_per_game * remaining_games

    # Determine window dates from games
    window_start = min(g.date for g in week_games) if week_games else None
    window_end = max(g.date for g in week_games) if week_games else None
    window_days = (window_end - window_start).days + 1 if window_start and window_end else 7

    return PlayerValue(
        nhl_id=nhl_id,
        name=player.full_name,
        team=team.abbrev,
        positions=positions,
        player_type=player_type,
        fpts_per_game=fpts_per_game,
        games_in_window=games_in_window,
        fillable_games=fillable_games,
        window_fpts=window_fpts,
        window_start=window_start,
        window_end=window_end,
        window_days=window_days,
        avg_toi=avg_toi,
        games_played=games_played,
        ros_value=ros_value,
        game_projections=game_projections,
    )


def compute_player_value_window(
    session: Session,
    nhl_id: int,
    roster: Roster,
    start_date: date,
    end_date: date,
    season: str = "20252026",
    forecast_fn: Optional[Callable[[int, date, float], float]] = None,
    as_of: Optional[date] = None,
) -> Optional[PlayerValue]:
    """Compute player value for a specific date window.

    This is the window-based version of compute_player_value. Instead of
    using yahoo_week, it evaluates the player over an arbitrary date range.

    This allows finding optimal windows like "3 games in 4 days" that may
    span fantasy week boundaries.

    Args:
        session: DB session
        nhl_id: NHL player ID
        roster: Current fantasy roster
        start_date: First day of window (inclusive)
        end_date: Last day of window (inclusive)
        season: Season string for stats lookup
        forecast_fn: Optional forecast function override

    Returns:
        PlayerValue with window-aware projection, or None if player not found.
    """
    if forecast_fn is None:
        forecast_fn = _default_forecast_fn

    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player:
        return None

    team = session.query(Team).filter(Team.team_id == player.team_id).first()
    if not team:
        return None

    # Determine positions
    if player.yahoo_positions:
        positions = [p.strip() for p in player.yahoo_positions.split(",")]
    else:
        nhl_to_yahoo = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}
        positions = [nhl_to_yahoo.get(player.position, player.position)]

    player_type = PlayerType.GOALIE if "G" in positions else PlayerType.SKATER

    if player_type == PlayerType.GOALIE:
        return None

    roster_player = RosterPlayer(
        name=player.full_name,
        team=team.abbrev,
        positions=positions,
        nhl_id=nhl_id,
    )

    # Get team's games in the window
    window_games = get_team_games_in_window(session, team.abbrev, start_date, end_date)
    games_in_window = len(window_games)

    # Get avg TOI from historical data (as of the decision date — start_date
    # is the start of the window but also the earliest point the decision
    # would be made, so anything on or after it is future data).
    cutoff = as_of if as_of is not None else start_date
    fpts_data = compute_fpts_per_gp(session, nhl_id, season, as_of=cutoff)
    avg_toi = fpts_data["avg_toi"] if fpts_data else 12.0
    games_played = fpts_data["gp"] if fpts_data else 0

    # Check if the player is already on the roster
    is_on_roster = any(p.nhl_id == nhl_id for p in roster.players)

    if is_on_roster:
        working_roster = roster
    else:
        working_roster = Roster(
            players=roster.players + [roster_player],
            roster_slot_settings=roster.roster_slot_settings,
        )

    # For each game day, check slot availability and project FPTS
    game_projections: dict[date, float] = {}
    fillable_games = 0

    for game in window_games:
        game_date = game.date

        teams_playing = get_teams_playing_on_date(game_date)
        playing_players = [
            p for p in working_roster.players if p.team in teams_playing
        ]

        if can_player_fill_slot(
            roster_player, playing_players, roster.roster_slot_settings
        ):
            projected = forecast_fn(nhl_id, game_date, avg_toi)
            game_projections[game_date] = projected
            fillable_games += 1

    window_fpts = sum(game_projections.values())
    fpts_per_game = window_fpts / fillable_games if fillable_games > 0 else 0.0

    # ROS value (count games from the decision date forward)
    remaining_games = get_team_remaining_games(session, team.abbrev, from_date=cutoff)
    ros_value = fpts_per_game * remaining_games

    window_days = (end_date - start_date).days + 1

    return PlayerValue(
        nhl_id=nhl_id,
        name=player.full_name,
        team=team.abbrev,
        positions=positions,
        player_type=player_type,
        fpts_per_game=fpts_per_game,
        games_in_window=games_in_window,
        fillable_games=fillable_games,
        window_fpts=window_fpts,
        window_start=start_date,
        window_end=end_date,
        window_days=window_days,
        avg_toi=avg_toi,
        games_played=games_played,
        ros_value=ros_value,
        game_projections=game_projections,
    )


def find_optimal_window(
    session: Session,
    nhl_id: int,
    roster: Roster,
    from_date: date,
    max_window_days: int = 7,
    season: str = "20252026",
    forecast_fn: Optional[Callable[[int, date, float], float]] = None,
    as_of: Optional[date] = None,
) -> Optional[PlayerValue]:
    """Find the window length that maximizes value for a player.

    Searches windows from 1 to max_window_days and returns the PlayerValue
    for the window that maximizes fillable FPTS.

    This captures burst opportunities like "3 fillable games in 4 days"
    that might be missed when looking at a full 7-day window.

    Args:
        session: DB session
        nhl_id: NHL player ID
        roster: Current fantasy roster
        from_date: Start date for window search
        max_window_days: Maximum window length to consider (default 7)
        season: Season string
        forecast_fn: Optional forecast function override

    Returns:
        PlayerValue for the optimal window, or None if player not found.
    """
    best_value: Optional[PlayerValue] = None
    best_fpts = -1.0

    for window_days in range(1, max_window_days + 1):
        end_date = from_date + timedelta(days=window_days - 1)

        value = compute_player_value_window(
            session, nhl_id, roster, from_date, end_date,
            season=season, forecast_fn=forecast_fn,
            as_of=as_of if as_of is not None else from_date,
        )

        if value is None:
            continue

        # Use window_fpts as the primary metric
        # Could also use efficiency (window_fpts / window_days) if we want
        # to prefer shorter windows for the same total value
        if value.window_fpts > best_fpts:
            best_fpts = value.window_fpts
            best_value = value

    return best_value


def find_optimal_window_simple(
    session: Session,
    nhl_id: int,
    roster: Roster,
    from_date: date,
    max_window_days: int = 7,
    season: str = "20252026",
    as_of: Optional[date] = None,
) -> Optional[PlayerValue]:
    """Find optimal window using simple historical FPTS/GP (no forecasting).

    Faster version for bulk evaluation of free agents.

    `as_of` caps which historical games feed the FPTS/GP baseline (no
    leakage). Defaults to `from_date` when not set.
    """
    cutoff = as_of if as_of is not None else from_date

    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player:
        return None

    team = session.query(Team).filter(Team.team_id == player.team_id).first()
    if not team:
        return None

    fpts_data = compute_fpts_per_gp(session, nhl_id, season, as_of=cutoff)
    if not fpts_data:
        return None

    if player.yahoo_positions:
        positions = [p.strip() for p in player.yahoo_positions.split(",")]
    else:
        nhl_to_yahoo = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}
        positions = [nhl_to_yahoo.get(player.position, player.position)]

    player_type = PlayerType.GOALIE if "G" in positions else PlayerType.SKATER
    if player_type == PlayerType.GOALIE:
        return None

    roster_player = RosterPlayer(
        name=player.full_name,
        team=team.abbrev,
        positions=positions,
        nhl_id=nhl_id,
    )

    is_on_roster = any(p.nhl_id == nhl_id for p in roster.players)
    if is_on_roster:
        working_roster = roster
    else:
        working_roster = Roster(
            players=roster.players + [roster_player],
            roster_slot_settings=roster.roster_slot_settings,
        )

    fpts_per_game = fpts_data["fpts_per_gp"]
    best_value: Optional[PlayerValue] = None
    best_fpts = -1.0

    for window_days in range(1, max_window_days + 1):
        end_date = from_date + timedelta(days=window_days - 1)
        window_games = get_team_games_in_window(session, team.abbrev, from_date, end_date)

        game_projections: dict[date, float] = {}
        fillable_games = 0

        for game in window_games:
            game_date = game.date
            teams_playing = get_teams_playing_on_date(game_date)
            playing_players = [
                p for p in working_roster.players if p.team in teams_playing
            ]

            if can_player_fill_slot(
                roster_player, playing_players, roster.roster_slot_settings
            ):
                game_projections[game_date] = fpts_per_game
                fillable_games += 1

        window_fpts = fillable_games * fpts_per_game

        if window_fpts > best_fpts:
            best_fpts = window_fpts
            remaining_games = get_team_remaining_games(session, team.abbrev, from_date=cutoff)

            best_value = PlayerValue(
                nhl_id=nhl_id,
                name=player.full_name,
                team=team.abbrev,
                positions=positions,
                player_type=player_type,
                fpts_per_game=fpts_per_game,
                games_in_window=len(window_games),
                fillable_games=fillable_games,
                window_fpts=window_fpts,
                window_start=from_date,
                window_end=end_date,
                window_days=window_days,
                avg_toi=fpts_data["avg_toi"],
                games_played=fpts_data["gp"],
                ros_value=fpts_per_game * remaining_games,
                game_projections=game_projections,
            )

    return best_value


def compute_player_value_simple(
    session: Session,
    nhl_id: int,
    yahoo_week: int,
    season: str = "20252026",
    as_of: Optional[date] = None,
) -> Optional[PlayerValue]:
    """Simplified player value without roster context.

    Uses historical FPTS/GP instead of forecasting. Useful for bulk
    evaluation of free agents where running the full forecast for every
    player would be too slow.

    Args:
        as_of: Knowledge-cutoff date. Historical stats are computed using
            only games strictly before this date, and ROS games are counted
            from this date forward. Defaults to today if not specified.

    Returns PlayerValue with games_this_week set but fillable_games
    equal to games_this_week (no slot check — assumes all games count).
    """
    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player:
        return None

    team = session.query(Team).filter(Team.team_id == player.team_id).first()
    if not team:
        return None

    fpts_data = compute_fpts_per_gp(session, nhl_id, season, as_of=as_of)
    if not fpts_data:
        return None

    if player.yahoo_positions:
        positions = [p.strip() for p in player.yahoo_positions.split(",")]
    else:
        nhl_to_yahoo = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}
        positions = [nhl_to_yahoo.get(player.position, player.position)]

    player_type = PlayerType.GOALIE if "G" in positions else PlayerType.SKATER

    # Goalies cannot be valued through the skater stats pipeline.
    if player_type == PlayerType.GOALIE:
        return None

    week_games = get_team_week_games(session, team.abbrev, yahoo_week)
    games_in_window = len(week_games)

    fpts_per_game = fpts_data["fpts_per_gp"]
    window_fpts = fpts_per_game * games_in_window

    remaining_games = get_team_remaining_games(session, team.abbrev, from_date=as_of)
    ros_value = fpts_per_game * remaining_games

    # Determine window dates from games
    window_start = min(g.date for g in week_games) if week_games else None
    window_end = max(g.date for g in week_games) if week_games else None
    window_days = (window_end - window_start).days + 1 if window_start and window_end else 7

    return PlayerValue(
        nhl_id=nhl_id,
        name=player.full_name,
        team=team.abbrev,
        positions=positions,
        player_type=player_type,
        fpts_per_game=fpts_per_game,
        games_in_window=games_in_window,
        fillable_games=games_in_window,  # assumes all games count
        window_fpts=window_fpts,
        window_start=window_start,
        window_end=window_end,
        window_days=window_days,
        avg_toi=fpts_data["avg_toi"],
        games_played=fpts_data["gp"],
        ros_value=ros_value,
        game_projections={g.date: fpts_per_game for g in week_games},
    )

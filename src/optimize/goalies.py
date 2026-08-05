"""Goalie streaming evaluator.

Scores goalie streams for single-game or multi-game pickups. Goalies
compete in the same candidate pool as skaters — a goalie projected at
7 FPTS for 1 start competes against a skater at 2.5 FPTS/game x 3 games.

Derives goalie game stats (saves, GA, wins, shutouts, FPTS) from the
shot_attempts table (has goalie_id on every shot) and the Game table
(has scores for win/loss determination).
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, or_, case, literal_column
from sqlalchemy.orm import Session

from src.core.models import Game, GoalieStart, Player, Team
from src.core.models.shot_attempts import ShotAttempt
from src.core.scoring import GOALIE_WEIGHTS
from src.optimize.models import GoalieStreamScore, PlayerValue, PlayerType


@dataclass
class GoalieGameStats:
    """Derived goalie stats for a single game."""

    nhl_id: int
    game_id: int
    game_date: date
    saves: int
    goals_against: int
    shots_against: int
    won: bool
    shutout: bool
    fpts: float


def compute_goalie_game_log(
    session: Session,
    nhl_id: int,
    season_prefix: int = 2025,
) -> list[GoalieGameStats]:
    """Compute per-game goalie stats from shot_attempts + Game scores.

    Derives saves, GA, wins, shutouts, and FPTS for every game
    this goalie appeared in.
    """
    game_id_min = season_prefix * 1_000_000 + 20_000
    game_id_max = season_prefix * 1_000_000 + 30_000

    # Get per-game saves and goals against from shot_attempts
    shot_stats = (
        session.query(
            ShotAttempt.game_id,
            func.count().filter(ShotAttempt.is_goal == False).label("saves"),
            func.count().filter(ShotAttempt.is_goal == True).label("goals_against"),
            func.count().label("shots_against"),
        )
        .filter(
            ShotAttempt.goalie_id == nhl_id,
            ShotAttempt.game_id > game_id_min,
            ShotAttempt.game_id < game_id_max,
            ShotAttempt.event_type.in_(["shot-on-goal", "goal"]),
        )
        .group_by(ShotAttempt.game_id)
        .all()
    )

    if not shot_stats:
        return []

    # Get goalie's team
    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player:
        return []
    goalie_team_id = player.team_id

    # Get game scores for win/loss
    game_ids = [s.game_id for s in shot_stats]
    games = (
        session.query(Game)
        .filter(Game.game_id.in_(game_ids))
        .all()
    )
    game_map = {g.game_id: g for g in games}

    results = []
    for s in shot_stats:
        game = game_map.get(s.game_id)
        if not game or game.home_score is None:
            continue

        # Determine win/loss
        if goalie_team_id == game.home_team_id:
            won = game.home_score > game.away_score
        else:
            won = game.away_score > game.home_score

        saves = s.saves or 0
        ga = s.goals_against or 0
        shutout = ga == 0

        fpts = (
            saves * GOALIE_WEIGHTS["saves"]
            + ga * GOALIE_WEIGHTS["goals_against"]
            + (GOALIE_WEIGHTS["wins"] if won else 0)
            + (GOALIE_WEIGHTS["shutouts"] if shutout else 0)
        )

        results.append(
            GoalieGameStats(
                nhl_id=nhl_id,
                game_id=s.game_id,
                game_date=game.date,
                saves=saves,
                goals_against=ga,
                shots_against=s.shots_against or 0,
                won=won,
                shutout=shutout,
                fpts=fpts,
            )
        )

    results.sort(key=lambda g: g.game_date, reverse=True)
    return results


def compute_goalie_fpts_per_start(
    session: Session,
    nhl_id: int,
    season_prefix: int = 2025,
) -> dict | None:
    """Compute average goalie FPTS/start from real game data.

    Returns dict with fpts_per_start, games_started, avg_saves,
    avg_goals_against, win_pct, save_pct, or None if no data.
    """
    game_log = compute_goalie_game_log(session, nhl_id, season_prefix)
    if not game_log:
        return None

    gp = len(game_log)
    total_fpts = sum(g.fpts for g in game_log)
    total_saves = sum(g.saves for g in game_log)
    total_ga = sum(g.goals_against for g in game_log)
    total_sa = sum(g.shots_against for g in game_log)
    wins = sum(1 for g in game_log if g.won)

    return {
        "fpts_per_start": total_fpts / gp,
        "games_started": gp,
        "avg_saves": total_saves / gp,
        "avg_goals_against": total_ga / gp,
        "win_pct": wins / gp,
        "save_pct": total_saves / total_sa if total_sa > 0 else 0,
        "total_fpts": total_fpts,
    }


def compute_opponent_softness(
    session: Session,
    opponent_abbrev: str,
    season_prefix: int = 2025,
) -> dict:
    """Compute how "soft" an opponent is for goalie streaming.

    Uses real shot data from shot_attempts to compute actual opposing
    goalie FPTS (not estimated from 30 shots/game assumption).
    """
    team = session.query(Team).filter(Team.abbrev == opponent_abbrev).first()
    if not team:
        return {"goals_per_game": 3.0, "goals_against_per_game": 3.0, "opp_goalie_fpts_avg": 5.0}

    game_id_min = season_prefix * 1_000_000 + 20_000
    game_id_max = season_prefix * 1_000_000 + 30_000

    games = (
        session.query(Game)
        .filter(
            Game.home_score.isnot(None),
            Game.game_id > game_id_min,
            Game.game_id < game_id_max,
            or_(
                Game.home_team_id == team.team_id,
                Game.away_team_id == team.team_id,
            ),
        )
        .all()
    )

    if not games:
        return {"goals_per_game": 3.0, "goals_against_per_game": 3.0, "opp_goalie_fpts_avg": 5.0}

    total_gf = 0
    total_ga = 0
    total_goalie_fpts = 0.0
    goalie_wins = 0

    for g in games:
        if g.home_team_id == team.team_id:
            gf, ga = g.home_score, g.away_score
            opp_won = g.away_score > g.home_score
        else:
            gf, ga = g.away_score, g.home_score
            opp_won = g.home_score > g.away_score

        total_gf += gf
        total_ga += ga

        # Get actual shots on goal against this team from shot_attempts
        sog_against = (
            session.query(func.count())
            .filter(
                ShotAttempt.game_id == g.game_id,
                ShotAttempt.opponent_team_id == team.team_id,
                ShotAttempt.event_type.in_(["shot-on-goal", "goal"]),
            )
            .scalar()
        ) or 30  # fallback if no shot data

        saves = sog_against - gf
        goalie_fpts = (
            saves * GOALIE_WEIGHTS["saves"]
            + gf * GOALIE_WEIGHTS["goals_against"]
            + (GOALIE_WEIGHTS["wins"] if opp_won else 0)
            + (GOALIE_WEIGHTS["shutouts"] if gf == 0 else 0)
        )
        total_goalie_fpts += goalie_fpts
        if opp_won:
            goalie_wins += 1

    gp = len(games)
    return {
        "goals_per_game": total_gf / gp,
        "goals_against_per_game": total_ga / gp,
        "opp_goalie_fpts_avg": total_goalie_fpts / gp,
        "opp_win_pct": goalie_wins / gp,
        "gp": gp,
    }


def compute_crease_share(
    session: Session,
    nhl_id: int,
    season_prefix: int = 2025,
    lookback_games: int = 15,
) -> float:
    """What fraction of their team's recent games did this goalie start?

    Uses shot_attempts to determine which goalie started each game
    (the goalie who faced shots). Falls back to GoalieStart table.

    Returns 0.0-1.0.
    """
    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player or not player.team_id:
        return 0.0

    game_id_min = season_prefix * 1_000_000 + 20_000
    game_id_max = season_prefix * 1_000_000 + 30_000

    # Get recent team games
    recent_games = (
        session.query(Game)
        .filter(
            or_(
                Game.home_team_id == player.team_id,
                Game.away_team_id == player.team_id,
            ),
            Game.home_score.isnot(None),
            Game.game_id > game_id_min,
            Game.game_id < game_id_max,
        )
        .order_by(Game.date.desc())
        .limit(lookback_games)
        .all()
    )

    if not recent_games:
        return 0.0

    # For each game, check if this goalie faced shots
    starts = 0
    for game in recent_games:
        faced_shots = (
            session.query(func.count())
            .filter(
                ShotAttempt.game_id == game.game_id,
                ShotAttempt.goalie_id == nhl_id,
            )
            .scalar()
        )
        if faced_shots and faced_shots > 0:
            starts += 1

    return starts / len(recent_games)


# Crease share thresholds for classifying goalie role
STARTER_THRESHOLD = 0.60  # >60% of recent starts = established starter
BACKUP_THRESHOLD = 0.30   # <30% = clearly a backup


def classify_goalie_role(crease_share: float) -> str:
    """Classify a goalie's role based on crease share.

    Returns: "starter", "committee", or "backup".
    """
    if crease_share >= STARTER_THRESHOLD:
        return "starter"
    elif crease_share >= BACKUP_THRESHOLD:
        return "committee"
    else:
        return "backup"


def is_back_to_back(
    session: Session,
    team_id: int,
    game_date: date,
) -> bool:
    """Check if the team played yesterday (back-to-back situation)."""
    from datetime import timedelta
    yesterday = game_date - timedelta(days=1)
    prior_game = (
        session.query(Game)
        .filter(
            Game.date == yesterday,
            or_(
                Game.home_team_id == team_id,
                Game.away_team_id == team_id,
            ),
        )
        .first()
    )
    return prior_game is not None


def predict_starts(
    session: Session,
    nhl_id: int,
    week_games: list[Game],
    crease_share: float,
) -> dict[int, bool]:
    """Deterministic guess: will this goalie start each game this week?

    Logic:
    - If confirmed/probable in GoalieStart table → use that (known truth)
    - Established starter (crease_share >= 60%): starts every game EXCEPT
      the second game of a back-to-back (assume backup gets B2Bs)
    - Committee goalie (30-60%): only project known starts, skip the rest
    - Backup (<30%): only project known starts

    Returns: {game_id: True/False}
    """
    role = classify_goalie_role(crease_share)
    predictions = {}

    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player or not player.team_id:
        return {g.game_id: False for g in week_games}
    team_id = player.team_id

    for game in week_games:
        # Check GoalieStart table first — if we have explicit data, use it
        goalie_start = (
            session.query(GoalieStart)
            .filter(
                GoalieStart.game_id == game.game_id,
                GoalieStart.nhl_id == nhl_id,
            )
            .first()
        )

        if goalie_start is not None:
            # Explicit data: confirmed or probable = yes
            predictions[game.game_id] = True
            continue

        # Check if another goalie on this team is confirmed/probable for
        # this game — if so, this goalie is NOT starting
        other_start = (
            session.query(GoalieStart)
            .filter(GoalieStart.game_id == game.game_id)
            .first()
        )
        if other_start is not None:
            predictions[game.game_id] = False
            continue

        # No explicit data for this game — make a guess based on role
        if role == "starter":
            # Starters play everything except B2B second nights
            if is_back_to_back(session, team_id, game.date):
                predictions[game.game_id] = False
            else:
                predictions[game.game_id] = True
        else:
            # Committee and backup goalies: don't project future starts
            # without explicit confirmation
            predictions[game.game_id] = False

    return predictions


def check_goalie_confirmed(
    session: Session,
    nhl_id: int,
    game_date: date,
) -> float | None:
    """Check starter confirmation for a goalie on a specific date.

    Returns:
        1.0 if confirmed, 0.7 if probable, None if no GoalieStart data.
    """
    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player or not player.team_id:
        return None

    game = (
        session.query(Game)
        .filter(
            Game.date == game_date,
            or_(
                Game.home_team_id == player.team_id,
                Game.away_team_id == player.team_id,
            ),
        )
        .first()
    )

    if not game:
        return None

    goalie_start = (
        session.query(GoalieStart)
        .filter(
            GoalieStart.game_id == game.game_id,
            GoalieStart.nhl_id == nhl_id,
        )
        .first()
    )

    if goalie_start is not None:
        return 1.0 if goalie_start.confirmed else 0.7
    return None


def evaluate_goalie_stream(
    session: Session,
    nhl_id: int,
    game_date: date,
    season_prefix: int = 2025,
) -> GoalieStreamScore | None:
    """Score a goalie for a single-game stream.

    Combines:
    1. Goalie's own talent (historical FPTS/start from shot_attempts)
    2. Opponent softness (how many goals they give up to opposing goalies)
    3. Starter confirmation
    4. Crease share
    """
    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player or not player.team_id:
        return None

    team = session.query(Team).filter(Team.team_id == player.team_id).first()
    if not team:
        return None

    # Find opponent for this game
    game = (
        session.query(Game)
        .filter(
            Game.date == game_date,
            or_(
                Game.home_team_id == team.team_id,
                Game.away_team_id == team.team_id,
            ),
        )
        .first()
    )
    if not game:
        return None

    opp_team_id = (
        game.away_team_id if game.home_team_id == team.team_id
        else game.home_team_id
    )
    opp_team = session.query(Team).filter(Team.team_id == opp_team_id).first()
    if not opp_team:
        return None

    # Goalie's own quality from real game data
    goalie_stats = compute_goalie_fpts_per_start(session, nhl_id, season_prefix)
    goalie_fpts_per_start = goalie_stats["fpts_per_start"] if goalie_stats else 5.0
    goalie_gp = goalie_stats["games_started"] if goalie_stats else 0

    # Opponent softness
    softness = compute_opponent_softness(session, opp_team.abbrev, season_prefix)

    # Blend goalie talent with opponent matchup
    # Weight: 60% goalie's own rate, 40% opponent softness
    opp_fpts = softness["opp_goalie_fpts_avg"]
    projected = goalie_fpts_per_start * 0.6 + opp_fpts * 0.4

    # Starter confirmation (for display only — start prediction is
    # handled deterministically by predict_starts for multi-game evals)
    confirmation = check_goalie_confirmed(session, nhl_id, game_date)
    crease = compute_crease_share(session, nhl_id, season_prefix)

    if confirmation == 1.0:
        conf_label = "confirmed"
    elif confirmation == 0.7:
        conf_label = "probable"
    else:
        role = classify_goalie_role(crease)
        conf_label = f"{role} ({crease:.0%} crease share)"

    reasoning = [
        f"Goalie quality: {goalie_fpts_per_start:.1f} FPTS/start ({goalie_gp} starts)",
        f"vs {opp_team.abbrev}: {softness['goals_per_game']:.1f} GF/G, "
        f"{opp_fpts:.1f} avg opposing goalie FPTS",
        f"Projected: {projected:.1f} FPTS ({conf_label})",
    ]

    return GoalieStreamScore(
        nhl_id=nhl_id,
        name=player.full_name,
        game_date=game_date,
        opponent=opp_team.abbrev,
        opponent_goals_per_game=softness["goals_per_game"],
        opponent_goals_against_per_game=softness["goals_against_per_game"],
        starter_confidence=confirmation if confirmation is not None else crease,
        projected_fpts=projected,
        crease_share=crease,
        reasoning=reasoning,
    )


def goalie_stream_to_player_value(
    session: Session,
    nhl_id: int,
    yahoo_week: int,
    season_prefix: int = 2025,
) -> PlayerValue | None:
    """Convert goalie stream evaluations into a PlayerValue.

    Evaluates all games the goalie's team plays this week, sums
    projected FPTS, and returns a PlayerValue that competes in the
    same pool as skaters.
    """
    player = session.query(Player).filter(Player.nhl_id == nhl_id).first()
    if not player or not player.team_id:
        return None

    team = session.query(Team).filter(Team.team_id == player.team_id).first()
    if not team:
        return None

    positions = ["G"]
    if player.yahoo_positions:
        positions = [p.strip() for p in player.yahoo_positions.split(",")]

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

    if not games:
        return None

    # Get goalie's historical FPTS and crease share
    goalie_stats = compute_goalie_fpts_per_start(session, nhl_id, season_prefix)
    crease = compute_crease_share(session, nhl_id, season_prefix)

    # Deterministic start predictions for each game this week
    start_predictions = predict_starts(session, nhl_id, games, crease)

    # Evaluate each game — only include projected starts
    game_projections: dict[date, float] = {}
    total_fpts = 0.0

    for game in games:
        if not start_predictions.get(game.game_id, False):
            continue  # not projected to start

        stream_score = evaluate_goalie_stream(
            session, nhl_id, game.date, season_prefix
        )
        if stream_score and stream_score.projected_fpts > 0:
            game_projections[game.date] = stream_score.projected_fpts
            total_fpts += stream_score.projected_fpts

    fillable_games = len(game_projections)
    fpts_per_game = total_fpts / fillable_games if fillable_games > 0 else 0.0

    # ROS value: fpts_per_start * remaining_games * crease_share
    # (crease share is still used for ROS because multi-month projection
    # needs the probabilistic view — too far out to predict individual starts)
    remaining = (
        session.query(Game)
        .filter(
            Game.date >= date.today(),
            or_(
                Game.home_team_id == team.team_id,
                Game.away_team_id == team.team_id,
            ),
        )
        .count()
    )
    ros_value = fpts_per_game * remaining * crease

    # Window dates from games
    window_start = min(g.date for g in games) if games else None
    window_end = max(g.date for g in games) if games else None
    window_days = (window_end - window_start).days + 1 if window_start and window_end else 7

    return PlayerValue(
        nhl_id=nhl_id,
        name=player.full_name,
        team=team.abbrev,
        positions=positions,
        player_type=PlayerType.GOALIE,
        fpts_per_game=fpts_per_game,
        games_in_window=len(games),
        fillable_games=fillable_games,
        window_fpts=total_fpts,
        window_start=window_start,
        window_end=window_end,
        window_days=window_days,
        avg_toi=60.0,
        games_played=goalie_stats["games_started"] if goalie_stats else 0,
        ros_value=ros_value,
        game_projections=game_projections,
    )

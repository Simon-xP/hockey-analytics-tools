"""Feature engineering pipeline for xG model.

Transforms raw game_events into feature-enriched shot_attempts ready for
model training/prediction. Handles coordinate normalization, geometric
features, and sequence features (rebounds, rushes, flurries).
"""

import math

from sqlalchemy import text

from src.core.models.shot_attempts import ShotAttempt, NET_X, NET_Y

SHOT_EVENT_TYPES = {"shot-on-goal", "missed-shot", "blocked-shot", "goal"}

# Events that happen in game flow (used for sequence features)
GAME_FLOW_EVENTS = SHOT_EVENT_TYPES | {
    "faceoff", "hit", "giveaway", "takeaway", "penalty",
}


def parse_time_to_seconds(time_str: str) -> int:
    """Convert 'MM:SS' to total seconds."""
    if not time_str:
        return 0
    parts = time_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def game_seconds_elapsed(period: int, time_in_period: str) -> int:
    """Total seconds elapsed from game start."""
    period_seconds = parse_time_to_seconds(time_in_period)
    return (period - 1) * 1200 + period_seconds


def normalize_coordinates(x: float, y: float, attacking_positive_x: bool):
    """Normalize coordinates so the attacking net is always at +x.

    Args:
        x, y: Raw NHL coordinates.
        attacking_positive_x: True if the shooting team attacks the +x net.

    Returns:
        (x_adj, y_adj) with attacking net at approximately (89, 0).
    """
    if attacking_positive_x:
        return x, y
    return -x, -y


def compute_distance_to_net(x_adj: float, y_adj: float) -> float:
    """Euclidean distance from adjusted coordinates to net center."""
    return math.sqrt((x_adj - NET_X) ** 2 + (y_adj - NET_Y) ** 2)


def compute_angle_to_net(x_adj: float, y_adj: float) -> float:
    """Angle from shot to net center, in degrees.

    0 degrees = straight on from directly in front.
    90 degrees = at the goal line beside/behind the net.
    """
    dx = NET_X - x_adj
    dy = abs(y_adj - NET_Y)
    if dx <= 0:
        # Behind or at the goal line
        return 90.0
    return math.degrees(math.atan2(dy, dx))


def parse_strength_state(situation_code: str, team_id: int, home_team_id: int):
    """Parse situation code into strength state string from shooter's perspective.

    Situation code format: positions 0-3 are:
      [0] = away goalie count, [1] = away skaters,
      [2] = home skaters, [3] = home goalie count

    Returns e.g. "5v5", "5v4" (shooter has more), "4v5" (shooter has fewer).
    """
    if not situation_code or len(situation_code) != 4:
        return None

    away_skaters = int(situation_code[1])
    home_skaters = int(situation_code[2])

    if team_id == home_team_id:
        return f"{home_skaters}v{away_skaters}"
    else:
        return f"{away_skaters}v{home_skaters}"


def determine_attacking_direction(events: list[dict], home_team_id: int) -> dict:
    """Determine which direction each team attacks in each period.

    Uses shot locations: the team's shots should cluster near the net they attack.

    Returns dict: {(team_id, period): bool} where True means team attacks +x net.
    """
    # Collect shot x-coords by team and period
    from collections import defaultdict
    shot_xs = defaultdict(list)

    for e in events:
        if e["event_type"] in SHOT_EVENT_TYPES and e["x_coord"] is not None:
            key = (e["team_id"], e["period"])
            shot_xs[key].append(e["x_coord"])

    directions = {}
    for (team_id, period), xs in shot_xs.items():
        if xs:
            avg_x = sum(xs) / len(xs)
            # If average x is positive, team is attacking the +x net
            directions[(team_id, period)] = avg_x > 0

    return directions


def build_shot_attempts_for_game(
    session, game_id: int, home_team_id: int, away_team_id: int,
    home_score_start: int = 0, away_score_start: int = 0,
) -> list[ShotAttempt]:
    """Build feature-enriched ShotAttempt rows from game_events for one game.

    Args:
        session: SQLAlchemy session.
        game_id: The game to process.
        home_team_id: Home team's ID.
        away_team_id: Away team's ID.
        home_score_start: Starting score for home team (0 for regular games).
        away_score_start: Starting score for away team (0 for regular games).

    Returns:
        List of ShotAttempt objects (not yet added to session).
    """
    # Fetch all events for this game, ordered by sort_order
    rows = session.execute(
        text(
            "SELECT event_id, period, period_type, time_in_period, time_remaining, "
            "event_type, situation_code, x_coord, y_coord, zone_code, "
            "player_1_id, player_2_id, team_id, shot_type, sort_order "
            "FROM game_events "
            "WHERE game_id = :game_id "
            "ORDER BY sort_order"
        ),
        {"game_id": game_id},
    ).fetchall()

    if not rows:
        return []

    # Convert to dicts for easier handling
    columns = [
        "event_id", "period", "period_type", "time_in_period", "time_remaining",
        "event_type", "situation_code", "x_coord", "y_coord", "zone_code",
        "player_1_id", "player_2_id", "team_id", "shot_type", "sort_order",
    ]
    events = [dict(zip(columns, row)) for row in rows]

    # Determine attacking direction per team per period
    directions = determine_attacking_direction(events, home_team_id)

    # Track running score for score_differential
    home_score = home_score_start
    away_score = away_score_start

    # Track recent events for sequence features
    last_event = None  # Last game-flow event
    last_shot = None  # Last shot attempt (for rebound/angle change)
    recent_team_shots = []  # (game_seconds, team_id) for flurry detection

    shot_attempts = []

    for e in events:
        event_type = e["event_type"]

        # Update score tracking on goals (before processing the goal as a shot)
        # We want score_differential at time of shot, before this goal
        score_diff_home = home_score - away_score

        is_shot = event_type in SHOT_EVENT_TYPES
        is_game_flow = event_type in GAME_FLOW_EVENTS

        if is_shot and e["x_coord"] is not None and e["player_1_id"] is not None:
            team_id = e["team_id"]
            period = e["period"]
            shooter_id = e["player_1_id"]

            # Goalie: player_2 for shots/goals, None for blocked shots
            goalie_id = e["player_2_id"] if event_type != "blocked-shot" else None

            # Attacking direction
            attacks_positive = directions.get((team_id, period))
            if attacks_positive is None:
                # Fallback: skip if we can't determine direction
                # (shouldn't happen with enough shots)
                attacks_positive = True

            # Normalize coordinates
            x_adj, y_adj = normalize_coordinates(
                e["x_coord"], e["y_coord"], attacks_positive
            )

            # Geometric features
            distance = compute_distance_to_net(x_adj, y_adj)
            angle = compute_angle_to_net(x_adj, y_adj)

            # Game state
            is_home = team_id == home_team_id
            score_diff = score_diff_home if is_home else -score_diff_home
            strength = parse_strength_state(
                e["situation_code"], team_id, home_team_id
            )

            # Timing
            gs = game_seconds_elapsed(period, e["time_in_period"])

            # Sequence features from last event
            time_since_last = None
            dist_from_last = None
            last_evt_type = None
            last_evt_x = None
            last_evt_y = None

            if last_event is not None:
                last_gs = game_seconds_elapsed(
                    last_event["period"], last_event["time_in_period"]
                )
                time_since_last = max(0, gs - last_gs)

                if last_event["x_coord"] is not None and last_event["y_coord"] is not None:
                    dist_from_last = math.sqrt(
                        (e["x_coord"] - last_event["x_coord"]) ** 2
                        + (e["y_coord"] - last_event["y_coord"]) ** 2
                    )
                    last_evt_x = last_event["x_coord"]
                    last_evt_y = last_event["y_coord"]

                last_evt_type = last_event["event_type"]

            # Rebound: shot within 3 seconds of previous shot on goal by same team
            is_rebound = False
            angle_change = None
            if last_shot is not None and last_shot["team_id"] == team_id:
                last_shot_gs = game_seconds_elapsed(
                    last_shot["period"], last_shot["time_in_period"]
                )
                time_since_shot = gs - last_shot_gs
                if 0 < time_since_shot <= 3:
                    is_rebound = True

                # Angle change from last shot (even if not rebound)
                if (last_shot["x_coord"] is not None
                        and last_shot["y_coord"] is not None):
                    last_attacks = directions.get(
                        (last_shot["team_id"], last_shot["period"]), True
                    )
                    lx, ly = normalize_coordinates(
                        last_shot["x_coord"], last_shot["y_coord"], last_attacks
                    )
                    last_angle = compute_angle_to_net(lx, ly)
                    angle_change = abs(angle - last_angle)

            # Rush: shot within 4 seconds of an event in neutral/defensive zone
            is_rush = False
            if last_event is not None and time_since_last is not None:
                if (time_since_last <= 4
                        and last_event["zone_code"] in ("N", "D")
                        and last_event["period"] == period):
                    is_rush = True

            # Flurry: number of shot attempts by same team in last 10 seconds
            recent_team_shots = [
                (t, tid) for t, tid in recent_team_shots if gs - t <= 10
            ]
            flurry_count = sum(1 for t, tid in recent_team_shots if tid == team_id)

            # Opponent team
            opponent_team_id = (
                away_team_id if team_id == home_team_id else home_team_id
            )

            shot = ShotAttempt(
                game_id=game_id,
                event_id=e["event_id"],
                shooter_id=shooter_id,
                goalie_id=goalie_id,
                team_id=team_id,
                opponent_team_id=opponent_team_id,
                period=period,
                period_type=e["period_type"],
                time_in_period=e["time_in_period"],
                game_seconds=gs,
                situation_code=e["situation_code"],
                strength_state=strength,
                score_differential=score_diff,
                is_home=is_home,
                x_coord=e["x_coord"],
                y_coord=e["y_coord"],
                x_adj=x_adj,
                y_adj=y_adj,
                distance_to_net=distance,
                angle_to_net=angle,
                event_type=event_type,
                shot_type=e["shot_type"],
                is_goal=(event_type == "goal"),
                time_since_last_event=time_since_last,
                distance_from_last_event=dist_from_last,
                last_event_type=last_evt_type,
                last_event_x=last_evt_x,
                last_event_y=last_evt_y,
                angle_change_from_last_shot=angle_change,
                is_rebound=is_rebound,
                is_rush=is_rush,
                flurry_count=flurry_count,
            )
            shot_attempts.append(shot)

            # Track for flurry
            recent_team_shots.append((gs, team_id))

            # Update last shot tracker
            last_shot = e

        # Update last game-flow event
        if is_game_flow:
            last_event = e

        # Update running score after processing
        if event_type == "goal":
            if e["team_id"] == home_team_id:
                home_score += 1
            else:
                away_score += 1

    return shot_attempts

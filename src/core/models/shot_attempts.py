from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Integer,
    String,
    ForeignKey,
    UniqueConstraint,
)

from src.core.models.base import Base

# Net positions on the NHL coordinate system (200x85 ft rink, center at 0,0)
NET_X = 89.0
NET_Y = 0.0


class ShotAttempt(Base):
    """
    Feature-enriched shot attempt for xG model training and prediction.

    One row per shot attempt (shot-on-goal, missed-shot, blocked-shot, goal).
    Built from game_events with derived geometric and sequence features.
    """
    __tablename__ = "shot_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False)
    event_id = Column(Integer, nullable=False)  # References game_events.event_id

    # Players
    shooter_id = Column(Integer, nullable=False)  # NHL player ID
    goalie_id = Column(Integer)  # Nullable for empty net

    # Teams
    team_id = Column(Integer, nullable=False)  # Shooting team
    opponent_team_id = Column(Integer)

    # Timing
    period = Column(Integer, nullable=False)
    period_type = Column(String(10))  # REG, OT
    time_in_period = Column(String(10))  # "MM:SS"
    game_seconds = Column(Integer)  # Total seconds elapsed in game

    # Game state
    situation_code = Column(String(10))  # "1551" = 5v5, etc.
    strength_state = Column(String(10))  # "5v5", "5v4", "4v5", "5v3", etc.
    score_differential = Column(Integer)  # From shooter's team perspective
    is_home = Column(Boolean)

    # Raw coordinates
    x_coord = Column(Float)
    y_coord = Column(Float)

    # Normalized coordinates (always oriented toward attacking net at +x)
    x_adj = Column(Float)
    y_adj = Column(Float)

    # Geometric features
    distance_to_net = Column(Float)  # Feet from net
    angle_to_net = Column(Float)  # Degrees, 0 = straight on, 90 = beside net

    # Shot info
    event_type = Column(String(50))  # shot-on-goal, missed-shot, blocked-shot, goal
    shot_type = Column(String(30))  # wrist, slap, snap, tip-in, backhand, etc.
    is_goal = Column(Boolean, nullable=False)  # The label for xG training

    # Sequence features (derived from prior events in the game)
    time_since_last_event = Column(Float)  # Seconds
    distance_from_last_event = Column(Float)  # Feet
    last_event_type = Column(String(50))
    last_event_x = Column(Float)
    last_event_y = Column(Float)

    # Rebound / rush / flurry
    angle_change_from_last_shot = Column(Float)  # Degrees
    is_rebound = Column(Boolean)  # Shot within 3s of prior SOG
    is_rush = Column(Boolean)  # Shot within 4s of neutral/defensive zone event
    flurry_count = Column(Integer)  # Shot attempts by same team in last 10s

    # xG prediction (populated after model is trained)
    xg = Column(Float)

    __table_args__ = (
        UniqueConstraint('game_id', 'event_id', name='uq_shot_attempt_game_event'),
    )

    def __repr__(self):
        return (
            f"<ShotAttempt {self.game_id}:{self.event_id} "
            f"{self.event_type} goal={self.is_goal}>"
        )

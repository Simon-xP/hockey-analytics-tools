from sqlalchemy import (
    Column,
    Float,
    Integer,
    String,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from src.core.models.base import Base


class GameEvent(Base):
    """
    Raw play-by-play event from the NHL API.

    One row per event per game. Events include shots, goals, hits, faceoffs,
    giveaways, takeaways, blocked shots, missed shots, penalties, stoppages.

    Source: /v1/gamecenter/{gameId}/play-by-play
    """
    __tablename__ = "game_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False)
    event_id = Column(Integer, nullable=False)  # NHL's eventId within the game

    # Timing
    period = Column(Integer, nullable=False)
    period_type = Column(String(10))  # REG, OT, SO
    time_in_period = Column(String(10))  # "MM:SS"
    time_remaining = Column(String(10))  # "MM:SS"

    # Event classification
    event_type = Column(String(50), nullable=False)  # shot-on-goal, goal, hit, etc.
    situation_code = Column(String(10))  # "1551" = 5v5, "1451" = PP, etc.

    # Location
    x_coord = Column(Float)
    y_coord = Column(Float)
    zone_code = Column(String(5))  # O, D, N

    # Players involved (no FK — events may reference players not yet in our DB)
    player_1_id = Column(Integer)  # shooter/hitter/winner
    player_2_id = Column(Integer)  # blocker/hittee/loser
    team_id = Column(Integer)

    # Shot-specific
    shot_type = Column(String(30))  # wrist, slap, snap, tip-in, backhand, etc.

    # Extra fields (assists, highlight clips, reasons, etc.)
    detail = Column(JSONB)

    # Sort order within the game (for sequencing events)
    sort_order = Column(Integer)

    __table_args__ = (
        UniqueConstraint('game_id', 'event_id',
                         name='uq_game_event'),
    )

    def __repr__(self):
        return f"<GameEvent {self.game_id}:{self.event_id} {self.event_type}>"


class PlayerShift(Base):
    """
    Individual player shift from NHL shift chart API.

    One row per shift per player per game.

    Source: /stats/rest/en/shiftcharts?cayenneExp=gameId={id}
    """
    __tablename__ = "player_shifts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False)
    player_id = Column(Integer, nullable=False)  # NHL player ID (no FK — may not be in players table)
    shift_number = Column(Integer, nullable=False)

    # Timing
    period = Column(Integer, nullable=False)
    start_time = Column(String(10), nullable=False)  # "MM:SS"
    end_time = Column(String(10), nullable=False)  # "MM:SS"
    duration = Column(String(10))  # "MM:SS"

    # Team
    team_id = Column(Integer)

    def __repr__(self):
        return (
            f"<PlayerShift {self.player_id} game={self.game_id} "
            f"P{self.period} #{self.shift_number}>"
        )

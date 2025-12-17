from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from src.core.models.base import Base


class Game(Base):
    __tablename__ = "games"

    game_id = Column(Integer, primary_key=True)  # NHL's game ID (e.g., 2025020021)
    date = Column(Date, nullable=False, index=True)
    start_time_utc = Column(DateTime)
    home_team_id = Column(Integer, ForeignKey("teams.team_id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.team_id"), nullable=False)
    yahoo_week = Column(Integer, index=True)

    # Relationships
    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])

    def __repr__(self):
        return f"<Game {self.game_id} on {self.date}>"


class GoalieStart(Base):
    __tablename__ = "goalie_starts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.team_id"), nullable=False)
    nhl_id = Column(Integer, ForeignKey("players.nhl_id"), nullable=False)
    confirmed = Column(Boolean, default=False)
    source = Column(String(50))  # "dailyfaceoff", "leftwinglock", etc.
    scraped_at = Column(DateTime)

    # Relationships
    game = relationship("Game")
    team = relationship("Team")
    player = relationship("Player")

    def __repr__(self):
        return f"<GoalieStart {self.nhl_id} for game {self.game_id}>"

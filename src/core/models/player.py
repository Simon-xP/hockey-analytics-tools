from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from src.core.models.base import Base


class Player(Base):
    __tablename__ = "players"

    nhl_id = Column(Integer, primary_key=True)  # NHL's official player ID
    full_name = Column(String(100), nullable=False)
    normalized_name = Column(String(100), index=True)  # For fast lookups
    team_id = Column(Integer, ForeignKey("teams.team_id"))
    position = Column(String(5))  # "C", "L", "R", "D", "G" (NHL codes)
    yahoo_player_id = Column(Integer)  # Yahoo's player ID
    yahoo_positions = Column(String(30))  # Yahoo positional eligibility: "C,LW"

    # Relationships
    team = relationship("Team", backref="players")
    aliases = relationship("PlayerAlias", back_populates="player")

    def __repr__(self):
        return f"<Player {self.full_name} ({self.nhl_id})>"


class PlayerAlias(Base):
    __tablename__ = "player_aliases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nhl_id = Column(Integer, ForeignKey("players.nhl_id"), nullable=False)
    alias = Column(String(100), nullable=False)
    normalized_alias = Column(String(100), index=True)
    source = Column(String(50))  # "yahoo", "naturalstattrick", "dailyfaceoff"

    # Relationships
    player = relationship("Player", back_populates="aliases")

    def __repr__(self):
        return f"<PlayerAlias '{self.alias}' -> {self.nhl_id}>"

from sqlalchemy import Column, Integer, String
from src.core.models.base import Base


class Team(Base):
    __tablename__ = "teams"

    team_id = Column(Integer, primary_key=True)  # NHL's team ID
    abbrev = Column(String(3), unique=True, nullable=False)  # "TOR", "BOS"
    full_name = Column(String(50), nullable=False)  # "Toronto Maple Leafs"
    short_name = Column(String(20))  # "Toronto"

    def __repr__(self):
        return f"<Team {self.abbrev}>"

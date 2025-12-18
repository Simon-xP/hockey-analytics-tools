from sqlalchemy import Column, Integer, String, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from src.core.models.base import Base


class SeasonStats(Base):
    """
    Season-level player stats from Natural Stat Trick.

    Each row represents one player's stats for one season in one situation
    (e.g., 5v5 individual counts for 2024-25 season).
    """
    __tablename__ = "season_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nhl_id = Column(Integer, ForeignKey("players.nhl_id"), nullable=False)
    season = Column(String(8), nullable=False)  # "20242025"
    situation = Column(String(50), nullable=False)  # "5v5_individual_counts"

    # Basic info (team_abbrev can be multiple teams like "BOS, FLA")
    team_abbrev = Column(String(20))
    position = Column(String(5))
    games_played = Column(Integer)
    toi = Column(Float)  # Time on ice

    # Scoring
    goals = Column(Integer)
    total_assists = Column(Integer)
    first_assists = Column(Integer)
    second_assists = Column(Integer)
    total_points = Column(Integer)
    ipp = Column(Float)  # Individual points percentage

    # Shooting
    shots = Column(Integer)
    sh_pct = Column(Float)  # Shooting percentage
    ixg = Column(Float)  # Individual expected goals

    # Chances
    icf = Column(Integer)  # Individual Corsi For
    iff = Column(Integer)  # Individual Fenwick For
    iscf = Column(Integer)  # Individual Scoring Chances For
    ihdcf = Column(Integer)  # Individual High Danger Chances For

    # Other
    rush_attempts = Column(Integer)
    rebounds_created = Column(Integer)
    pim = Column(Integer)
    total_penalties = Column(Integer)
    penalties_drawn = Column(Integer)
    giveaways = Column(Integer)
    takeaways = Column(Integer)
    hits = Column(Integer)
    hits_taken = Column(Integer)
    shots_blocked = Column(Integer)
    faceoffs_won = Column(Integer)
    faceoffs_lost = Column(Integer)
    faceoffs_pct = Column(Float)

    # Relationships
    player = relationship("Player", backref="season_stats")

    # Unique constraint: one row per player/season/situation combo
    __table_args__ = (
        UniqueConstraint('nhl_id', 'season', 'situation', name='uq_player_season_situation'),
    )

    def __repr__(self):
        return f"<SeasonStats {self.nhl_id} {self.season} {self.situation}>"

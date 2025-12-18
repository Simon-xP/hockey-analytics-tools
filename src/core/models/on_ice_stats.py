from sqlalchemy import Column, Integer, String, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from src.core.models.base import Base


class OnIceStats(Base):
    """
    On-ice player stats from Natural Stat Trick.

    Each row represents one player's on-ice stats for one season/situation
    (e.g., 5v5 on-ice counts for 2025-26 season).

    On-ice stats measure what happens when a player is on the ice,
    not their individual contributions.
    """
    __tablename__ = "on_ice_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nhl_id = Column(Integer, ForeignKey("players.nhl_id"), nullable=False)
    season = Column(String(8), nullable=False)  # "20252026"
    situation = Column(String(50), nullable=False)  # "5v5_on-ice_counts"

    # Basic info
    team_abbrev = Column(String(20))
    position = Column(String(5))
    games_played = Column(Integer)
    toi = Column(Float)

    # Corsi (shot attempts)
    cf = Column(Integer)   # Corsi For
    ca = Column(Integer)   # Corsi Against
    cf_pct = Column(Float)  # CF%

    # Fenwick (unblocked shot attempts)
    ff = Column(Integer)
    fa = Column(Integer)
    ff_pct = Column(Float)

    # Shots
    sf = Column(Integer)
    sa = Column(Integer)
    sf_pct = Column(Float)

    # Goals
    gf = Column(Integer)
    ga = Column(Integer)
    gf_pct = Column(Float)

    # Expected goals
    xgf = Column(Float)
    xga = Column(Float)
    xgf_pct = Column(Float)

    # Scoring chances
    scf = Column(Integer)
    sca = Column(Integer)
    scf_pct = Column(Float)

    # High danger
    hdcf = Column(Integer)
    hdca = Column(Integer)
    hdcf_pct = Column(Float)
    hdgf = Column(Integer)
    hdga = Column(Integer)
    hdgf_pct = Column(Float)

    # Medium danger
    mdcf = Column(Integer)
    mdca = Column(Integer)
    mdcf_pct = Column(Float)
    mdgf = Column(Integer)
    mdga = Column(Integer)
    mdgf_pct = Column(Float)

    # Low danger
    ldcf = Column(Integer)
    ldca = Column(Integer)
    ldcf_pct = Column(Float)
    ldgf = Column(Integer)
    ldga = Column(Integer)
    ldgf_pct = Column(Float)

    # On-ice percentages
    on_ice_sh_pct = Column(Float)
    on_ice_sv_pct = Column(Float)
    pdo = Column(Float)

    # Zone starts
    off_zone_starts = Column(Integer)
    neu_zone_starts = Column(Integer)
    def_zone_starts = Column(Integer)
    on_the_fly_starts = Column(Integer)
    off_zone_start_pct = Column(Float)

    # Zone faceoffs
    off_zone_faceoffs = Column(Integer)
    neu_zone_faceoffs = Column(Integer)
    def_zone_faceoffs = Column(Integer)
    off_zone_faceoff_pct = Column(Float)

    # Relationships
    player = relationship("Player", backref="on_ice_stats")

    __table_args__ = (
        UniqueConstraint('nhl_id', 'season', 'situation', name='uq_onice_player_season_situation'),
    )

    def __repr__(self):
        return f"<OnIceStats {self.nhl_id} {self.season} {self.situation}>"

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from src.core.models.base import Base


class GameIndividualStats(Base):
    """
    Per-game individual player stats from Natural Stat Trick (per-60 rates).

    Each row represents one player's per-60 rates for one game in one situation.
    Sourced from NST playerreport.php?v=g&rate=y (game log, rates view).

    All stat columns are per-60 minute rates except:
    - toi: raw game minutes
    - ipp, sh_pct: percentages
    """
    __tablename__ = "game_individual_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nhl_id = Column(Integer, ForeignKey("players.nhl_id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=True)
    game_date = Column(Date, nullable=False)
    season = Column(String(8), nullable=False)  # "20242025"
    situation = Column(String(50), nullable=False)  # "5v5", "all"

    # Game context
    team_abbrev = Column(String(10))
    opponent_abbrev = Column(String(10))
    is_home = Column(Boolean)

    # Time on ice (raw minutes, not per-60)
    toi = Column(Float)

    # Scoring (per 60)
    goals_per_60 = Column(Float)
    total_assists_per_60 = Column(Float)
    first_assists_per_60 = Column(Float)
    second_assists_per_60 = Column(Float)
    total_points_per_60 = Column(Float)

    # Percentages (not per-60)
    ipp = Column(Float)
    sh_pct = Column(Float)

    # Shooting (per 60)
    shots_per_60 = Column(Float)
    ixg_per_60 = Column(Float)

    # Chances (per 60)
    icf_per_60 = Column(Float)
    iff_per_60 = Column(Float)
    iscf_per_60 = Column(Float)
    ihdcf_per_60 = Column(Float)

    # Other (per 60)
    rush_attempts_per_60 = Column(Float)
    rebounds_created_per_60 = Column(Float)
    pim_per_60 = Column(Float)
    total_penalties_per_60 = Column(Float)
    penalties_drawn_per_60 = Column(Float)
    giveaways_per_60 = Column(Float)
    takeaways_per_60 = Column(Float)
    hits_per_60 = Column(Float)
    hits_taken_per_60 = Column(Float)
    shots_blocked_per_60 = Column(Float)
    faceoffs_won_per_60 = Column(Float)
    faceoffs_lost_per_60 = Column(Float)

    # Relationships
    player = relationship("Player", backref="game_individual_stats")
    game = relationship("Game", backref="game_individual_stats")

    __table_args__ = (
        UniqueConstraint('nhl_id', 'game_date', 'situation',
                         name='uq_game_individual_player_date_situation'),
    )

    def __repr__(self):
        return (
            f"<GameIndividualStats {self.nhl_id} "
            f"game={self.game_id} {self.situation}>"
        )


class GameOnIceStats(Base):
    """
    Per-game on-ice player stats from Natural Stat Trick (per-60 rates).

    Each row represents one player's on-ice per-60 rates for one game/situation.
    Sourced from NST playerreport.php?v=g&stdoi=oi&rate=y.

    All stat columns are per-60 minute rates except:
    - toi: raw game minutes
    - *_pct columns: percentages
    - on_ice_sh_pct, on_ice_sv_pct, pdo: percentages
    """
    __tablename__ = "game_on_ice_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nhl_id = Column(Integer, ForeignKey("players.nhl_id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=True)
    game_date = Column(Date, nullable=False)
    season = Column(String(8), nullable=False)  # "20242025"
    situation = Column(String(50), nullable=False)  # "5v5", "all"

    # Game context
    team_abbrev = Column(String(10))
    opponent_abbrev = Column(String(10))
    is_home = Column(Boolean)

    # Time on ice (raw minutes, not per-60)
    toi = Column(Float)

    # Corsi (per 60 + percentage)
    cf_per_60 = Column(Float)
    ca_per_60 = Column(Float)
    cf_pct = Column(Float)

    # Fenwick (per 60 + percentage)
    ff_per_60 = Column(Float)
    fa_per_60 = Column(Float)
    ff_pct = Column(Float)

    # Shots (per 60 + percentage)
    sf_per_60 = Column(Float)
    sa_per_60 = Column(Float)
    sf_pct = Column(Float)

    # Goals (per 60 + percentage)
    gf_per_60 = Column(Float)
    ga_per_60 = Column(Float)
    gf_pct = Column(Float)

    # Expected goals (per 60 + percentage)
    xgf_per_60 = Column(Float)
    xga_per_60 = Column(Float)
    xgf_pct = Column(Float)

    # Scoring chances (per 60 + percentage)
    scf_per_60 = Column(Float)
    sca_per_60 = Column(Float)
    scf_pct = Column(Float)

    # High danger (per 60 + percentage)
    hdcf_per_60 = Column(Float)
    hdca_per_60 = Column(Float)
    hdcf_pct = Column(Float)
    hdgf_per_60 = Column(Float)
    hdga_per_60 = Column(Float)
    hdgf_pct = Column(Float)

    # Medium danger (per 60 + percentage)
    mdcf_per_60 = Column(Float)
    mdca_per_60 = Column(Float)
    mdcf_pct = Column(Float)
    mdgf_per_60 = Column(Float)
    mdga_per_60 = Column(Float)
    mdgf_pct = Column(Float)

    # Low danger (per 60 + percentage)
    ldcf_per_60 = Column(Float)
    ldca_per_60 = Column(Float)
    ldcf_pct = Column(Float)
    ldgf_per_60 = Column(Float)
    ldga_per_60 = Column(Float)
    ldgf_pct = Column(Float)

    # On-ice percentages (not per-60)
    on_ice_sh_pct = Column(Float)
    on_ice_sv_pct = Column(Float)
    pdo = Column(Float)

    # Zone starts (per 60)
    off_zone_starts_per_60 = Column(Float)
    neu_zone_starts_per_60 = Column(Float)
    def_zone_starts_per_60 = Column(Float)
    on_the_fly_starts_per_60 = Column(Float)
    off_zone_start_pct = Column(Float)  # percentage, not per-60

    # Relationships
    player = relationship("Player", backref="game_on_ice_stats")
    game = relationship("Game", backref="game_on_ice_stats")

    __table_args__ = (
        UniqueConstraint('nhl_id', 'game_date', 'situation',
                         name='uq_game_onice_player_date_situation'),
    )

    def __repr__(self):
        return (
            f"<GameOnIceStats {self.nhl_id} "
            f"game={self.game_id} {self.situation}>"
        )

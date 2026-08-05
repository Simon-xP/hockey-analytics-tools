"""RAPM player ratings.

Stores the output of the RAPM ridge regression model — one row per
player per model run. Offensive and defensive ratings are computed
from separate one-sided models.
"""

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)

from src.core.models.base import Base


class PlayerRating(Base):
    __tablename__ = "player_ratings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, nullable=False)
    model_version = Column(String(32), nullable=False)
    seasons = Column(String(64), nullable=False)
    rating_off = Column(Float, nullable=False)
    rating_def = Column(Float)
    toi_minutes = Column(Float, nullable=False)
    percentile_off = Column(SmallInteger)
    percentile_def = Column(SmallInteger)
    elevation_off = Column(Float)
    computed_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("player_id", "model_version", "seasons",
                         name="uq_player_rating"),
    )

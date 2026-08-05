"""Shift segments for RAPM analysis.

A shift segment is a maximal time interval within a period where the
on-ice personnel do not change. Every shift start or end by any skater
creates a segment boundary.

Segments are the unit of observation for RAPM (Regularized Adjusted
Plus-Minus) — each segment contributes one or two rows to the regression
design matrix depending on the model formulation.
"""

from sqlalchemy import (
    ARRAY,
    Column,
    Float,
    Index,
    Integer,
    SmallInteger,
    String,
    ForeignKey,
    UniqueConstraint,
)

from src.core.models.base import Base


class ShiftSegment(Base):
    __tablename__ = "shift_segments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False)
    period = Column(SmallInteger, nullable=False)
    start_seconds = Column(SmallInteger, nullable=False)
    end_seconds = Column(SmallInteger, nullable=False)
    duration_seconds = Column(SmallInteger, nullable=False)
    situation = Column(String(8), nullable=False)
    score_state = Column(SmallInteger, nullable=False, default=0)
    home_skater_ids = Column(ARRAY(Integer), nullable=False)
    away_skater_ids = Column(ARRAY(Integer), nullable=False)
    home_xgf = Column(Float, nullable=False, default=0.0)
    away_xgf = Column(Float, nullable=False, default=0.0)

    __table_args__ = (
        Index("ix_shift_segments_game_period", "game_id", "period"),
        Index("ix_shift_segments_situation", "situation"),
    )

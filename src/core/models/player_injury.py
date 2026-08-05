from datetime import datetime

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Index, Integer, String, Text,
)

from src.core.models.base import Base


class PlayerInjury(Base):
    """Structured injury record sourced from Daily Faceoff.

    Each row is one distinct news blurb for one player. Dedup is by
    `news_hash` (sha256 of the blurb text) so re-scrapes don't re-LLM
    the same content. To get a player's *current* injury, select the
    newest row for that nhl_id.

    Raw fields (`injury_status`, `game_time_decision`, `news_details`)
    come directly from Daily Faceoff. Structured fields (`body_part`,
    `severity`, `timeline_days_*`, `expected_return`) are parsed from
    `news_details` by the LLM — any of them may be null when the blurb
    doesn't state them.
    """
    __tablename__ = "player_injuries"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Who
    nhl_id = Column(Integer, nullable=True, index=True)
    player_name = Column(String(128), nullable=False)
    team_abbrev = Column(String(8), nullable=True, index=True)
    position = Column(String(8), nullable=True)

    # Raw DF fields
    injury_status = Column(String(32), nullable=True)
    game_time_decision = Column(Boolean, nullable=False, default=False)
    news_details = Column(Text, nullable=True)
    news_date = Column(DateTime, nullable=True, index=True)
    news_hash = Column(String(32), unique=True, nullable=False, index=True)

    # LLM-parsed structured fields (any may be null if unknown)
    category = Column(String(32), nullable=True, index=True)  # injury|goalie_start|scratch|personal|transaction|return
    body_part = Column(String(64), nullable=True)
    severity = Column(String(32), nullable=True)  # day-to-day | week-to-week | month-plus | season | unknown
    timeline_days_min = Column(Integer, nullable=True)
    timeline_days_max = Column(Integer, nullable=True)
    expected_return = Column(Date, nullable=True)
    summary = Column(String(240), nullable=True)

    llm_parsed = Column(Boolean, nullable=False, default=False)
    scraped_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_player_injuries_nhl_news_date", "nhl_id", "news_date"),
    )

    def __repr__(self):
        return f"<PlayerInjury {self.player_name} {self.injury_status} {self.body_part}>"

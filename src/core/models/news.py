from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, Index
from sqlalchemy.dialects.postgresql import JSONB

from src.core.models.base import Base


class NewsItem(Base):
    """A single scraped tweet, classified once and persisted.

    The LLM (or regex fallback) runs exactly once per unique tweet at
    ingest time. The resulting `snippets` array — each snippet being
    one fantasy-actionable fact extracted from the tweet — is stored
    inline as JSONB so the API can serve it without re-classifying.

    Identity is the 16-char sha256 of the normalized tweet text. Same
    hash function as the LLM cache so we can reconcile if needed.
    """
    __tablename__ = "news_items"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Stable content hash — dedup key across scrapes
    text_hash = Column(String(32), unique=True, nullable=False, index=True)

    # Source tweet
    source_handle = Column(String(64))  # "@PFTCommenter" or empty
    text = Column(Text, nullable=False)

    # Classified output: list of {category, category_label, category_color,
    # summary, player_name, injury_type, team_tag}
    snippets = Column(JSONB, nullable=False)

    # When we first saw this tweet (used for ordering and "latest")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_news_items_created_desc", created_at.desc()),
    )

    def __repr__(self):
        return f"<NewsItem {self.text_hash} {self.source_handle}>"

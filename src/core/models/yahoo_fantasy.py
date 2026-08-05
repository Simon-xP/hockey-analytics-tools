"""Yahoo Fantasy league data models.

Stores draft picks and transactions to enable roster reconstruction
at any point in time for backtesting.
"""

from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship

from src.core.models.base import Base


class YahooDraftPick(Base):
    """A single draft pick from a Yahoo Fantasy league."""

    __tablename__ = "yahoo_draft_picks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    league_key = Column(String(50), nullable=False, index=True)
    pick_number = Column(Integer, nullable=False)
    round_number = Column(Integer, nullable=False)
    fantasy_team_key = Column(String(50), nullable=False)
    fantasy_team_name = Column(String(100), nullable=False)
    yahoo_player_id = Column(Integer, nullable=False, index=True)
    player_name = Column(String(100), nullable=False)
    nhl_team_abbrev = Column(String(10))
    position = Column(String(20))
    eligible_positions = Column(String(50))  # comma-separated: "C,LW,RW"

    # Link to our Player table if we can resolve
    nhl_id = Column(Integer, ForeignKey("players.nhl_id"), nullable=True)

    __table_args__ = (
        Index("ix_draft_league_pick", "league_key", "pick_number"),
    )


class YahooTransaction(Base):
    """A single transaction (add, drop, or trade) from a Yahoo Fantasy league."""

    __tablename__ = "yahoo_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    league_key = Column(String(50), nullable=False, index=True)
    transaction_id = Column(Integer, nullable=False)
    transaction_type = Column(String(20), nullable=False)  # add, drop, add/drop, trade
    timestamp = Column(DateTime, nullable=False, index=True)

    # Player involved
    yahoo_player_id = Column(Integer, nullable=False, index=True)
    player_name = Column(String(100), nullable=False)
    nhl_team_abbrev = Column(String(10))
    position = Column(String(20))

    # Action for this player in this transaction
    action = Column(String(10), nullable=False)  # "add" or "drop"

    # Team involved
    fantasy_team_key = Column(String(50))
    fantasy_team_name = Column(String(100))

    # Link to our Player table if we can resolve
    nhl_id = Column(Integer, ForeignKey("players.nhl_id"), nullable=True)

    __table_args__ = (
        Index("ix_tx_league_time", "league_key", "timestamp"),
        Index("ix_tx_league_player", "league_key", "yahoo_player_id"),
    )


class TeamRoster(Base):
    """Current roster membership for every team in a Yahoo Fantasy league.

    Maintained incrementally: insert on add, delete on drop.
    For historical roster queries, reconstruct from YahooDraftPick + YahooTransaction.
    """

    __tablename__ = "team_rosters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    league_key = Column(String(50), nullable=False, index=True)
    team_key = Column(String(50), nullable=False)
    nhl_id = Column(Integer, ForeignKey("players.nhl_id"), nullable=False)

    # Yahoo's own view of the player, as reported on the roster endpoint.
    # These are what decide IR eligibility, and Yahoo is the authority on it:
    # a player may only be moved to an IR slot when Yahoo says IR / IR-LT /
    # IR-NR, regardless of what an injury report says.
    yahoo_status = Column(String(16))  # "IR", "IR-LT", "IR-NR", "O", "DTD", "NA", None
    selected_position = Column(String(8))  # slot they currently occupy: "C", "BN", "IR", ...

    __table_args__ = (
        Index("ix_roster_league_team", "league_key", "team_key"),
        Index("ix_roster_league_player", "league_key", "nhl_id", unique=True),
    )

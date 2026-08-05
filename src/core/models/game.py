from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from src.core.models.base import Base


class Game(Base):
    __tablename__ = "games"

    game_id = Column(Integer, primary_key=True)  # NHL's game ID (e.g., 2025020021)
    date = Column(Date, nullable=False, index=True)
    start_time_utc = Column(DateTime)
    home_team_id = Column(Integer, ForeignKey("teams.team_id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.team_id"), nullable=False)
    yahoo_week = Column(Integer, index=True)
    home_score = Column(Integer)
    away_score = Column(Integer)

    # Relationships
    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])

    def __repr__(self):
        return f"<Game {self.game_id} on {self.date}>"


class GoalieStart(Base):
    """One *observation* of a reported starting goalie, at a point in time.

    This table is append-only, and that is the whole point. Starting-goalie
    reports firm up over the course of game day: a name that is "Expected"
    at 10am is often "Confirmed" by 4pm, and sometimes it changes outright.

    If this table stored one mutable row per (game, goalie), a backtest
    evaluating a Monday decision would read Thursday afternoon's confirmed
    starter. Every goalie stream would look inspired and the measured edge
    would be pure leakage, with nothing in the output that looks wrong.
    Appending instead means the history of what was knowable *when* is
    preserved, and `observed_at` is what makes a decision reproducible.

    Read this table through `latest_start_reports` in
    `src/core/queries/goalie_starts.py`, never directly. That helper takes
    an `as_of` timestamp and returns the most recent observation strictly
    before it, which is the only view a decision is entitled to.

    `confirmation` keeps the source's own wording rather than collapsing
    straight to a boolean, because the wording is the signal: "Confirmed"
    and "Likely" should not feed the same probability into a projection.
    `confirmed` stays as a boolean shorthand for the strongest tier.
    """

    __tablename__ = "goalie_starts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.team_id"), nullable=False)
    nhl_id = Column(Integer, ForeignKey("players.nhl_id"), nullable=False)
    confirmed = Column(Boolean, default=False)
    confirmation = Column(String(20))  # "Confirmed", "Expected", "Likely", ...
    source = Column(String(50))  # "dailyfaceoff", "leftwinglock", etc.

    # When this observation was made. The temporal key for every read.
    observed_at = Column(DateTime, nullable=False, index=True)

    # Relationships
    game = relationship("Game")
    team = relationship("Team")
    player = relationship("Player")

    __table_args__ = (
        # One row per observation, so the same report seen twice at
        # different times is two rows. Deliberately not unique on
        # (game_id, nhl_id): that constraint is what would force an upsert.
        UniqueConstraint(
            "game_id", "nhl_id", "observed_at", "source",
            name="uq_goalie_start_observation",
        ),
        Index("ix_goalie_starts_game_observed", "game_id", "observed_at"),
    )

    def __repr__(self):
        return (
            f"<GoalieStart {self.nhl_id} for game {self.game_id} "
            f"({self.confirmation})>"
        )

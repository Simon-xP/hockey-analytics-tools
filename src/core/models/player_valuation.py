from sqlalchemy import Column, DateTime, Float, Integer, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from src.core.models.base import Base


class PlayerValuation(Base):
    __tablename__ = "player_valuations"

    nhl_id = Column(
        Integer,
        ForeignKey("players.nhl_id"),
        primary_key=True,
    )

    fpts_per_game = Column(Float, nullable=False)
    avg_toi = Column(Float)
    games_played = Column(Integer)

    upside_score = Column(Float, default=0.0)
    opportunity_score = Column(Float, default=0.0)

    # Per-game forecasts for upcoming games.
    # Each entry: {"game_id": int, "date": "YYYY-MM-DD", "fpts": float,
    #              "opp_team_id": int, "home_team_id": int}
    game_forecasts = Column(JSONB, nullable=False, default=list)

    computed_at = Column(DateTime, server_default=func.now(), nullable=False)

    player = relationship("Player", backref="valuation")

    def forecasts_for_week(self, yahoo_week, session):
        """Filter game_forecasts to a specific Yahoo week."""
        from src.core.models.game import Game

        week_game_ids = {
            g.game_id
            for g in session.query(Game.game_id)
            .filter(Game.yahoo_week == yahoo_week)
            .all()
        }
        return [f for f in (self.game_forecasts or []) if f["game_id"] in week_game_ids]

    def forecasts_in_window(self, start_date, end_date):
        """Filter game_forecasts to a date range (inclusive)."""
        start_str = str(start_date)
        end_str = str(end_date)
        return [
            f
            for f in (self.game_forecasts or [])
            if start_str <= f["date"] <= end_str
        ]

"""TOI prediction model.

Predicts time on ice per situation for a player's next game. Starts with
a rolling EWMA baseline (which is surprisingly hard to beat for TOI
prediction), with an optional XGBoost model that adds opponent/context.

TOI is the most stable component of the prediction chain — a player's
ice time doesn't swing wildly unless there's an injury or coaching change.
"""

from datetime import date

import numpy as np

from src.predict.forecasting.features import (
    load_player_game_stats,
    ewma,
)


class TOIPredictor:
    """Predict TOI per situation using weighted rolling average.

    Uses EWMA with configurable half-life. This is the baseline TOI
    predictor — it's simple but effective because TOI is highly
    autocorrelated game-to-game.
    """

    def __init__(self, default_half_life: int = 5):
        self.default_half_life = default_half_life

    def predict(
        self,
        session,
        player_id: int,
        situation: str,
        game_date: date,
        current_season_start_year: int,
        is_b2b: bool = False,
        as_of: date | None = None,
    ) -> float:
        """Predict TOI in seconds for a player's next game in a situation.

        Args:
            session: SQLAlchemy session.
            player_id: NHL player ID.
            situation: "5v5", "pp", "pk", "other_combined".
            game_date: Date of the game to predict.
            current_season_start_year: e.g. 2025 for 2025-26 season.
            is_b2b: Whether this is a back-to-back game.
            as_of: Temporal cutoff. If provided, only uses data before
                this date (for backtesting). Defaults to game_date.

        Returns:
            Predicted TOI in seconds. Returns 0 if no prior data.
        """
        cutoff = min(game_date, as_of) if as_of is not None else game_date

        query_situation = situation
        if situation == "other":
            query_situation = "other_combined"

        season_start_gid = current_season_start_year * 1_000_000
        games = load_player_game_stats(
            session, player_id, query_situation, cutoff,
            season_start_game_id=season_start_gid,
        )

        if not games:
            # No current season data — return 0.
            # A player with zero games this season is likely not on an
            # NHL roster (injured, AHL, retired). Falling back to prior
            # season data would produce misleading predictions for players
            # like aging veterans who played last year but not this year.
            return 0.0

        toi_values = [g["toi_seconds"] for g in games]
        predicted_toi = ewma(toi_values, self.default_half_life)

        # Back-to-back adjustment: typical TOI drop is ~5-8%
        if is_b2b:
            predicted_toi *= 0.95

        return max(0.0, predicted_toi)

    def predict_all_situations(
        self,
        session,
        player_id: int,
        game_date: date,
        current_season_start_year: int,
        is_b2b: bool = False,
    ) -> dict[str, float]:
        """Predict TOI for all situations.

        Returns:
            Dict of situation -> predicted TOI in seconds.
        """
        results = {}
        for situation in ["5v5", "pp", "pk", "other"]:
            results[situation] = self.predict(
                session, player_id, situation, game_date,
                current_season_start_year, is_b2b,
            )
        return results

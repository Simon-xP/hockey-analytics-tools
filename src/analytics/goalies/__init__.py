"""Derived goalie metrics.

`game_log` turns raw shots, shifts, and scores into one stat line per
goalie per game. Later phases add the goalie-facing expected goals model
and the rate metrics built on top of it.

This is the `analytics` layer: it computes what happened, not what will
happen next. Forecasting lives in `src/predict/goalies/`.
"""

from src.analytics.goalies.game_log import (
    GoalieGameRow,
    build_goalie_rows,
    known_goalie_ids,
)

__all__ = ["GoalieGameRow", "build_goalie_rows", "known_goalie_ids"]

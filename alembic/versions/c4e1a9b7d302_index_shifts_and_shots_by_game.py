"""index player_shifts and shot_attempts for per-game lookups

Every consumer of shift data loads it one game at a time: the shift-event
correlation engine, RAPM, and now the goalie game log. `player_shifts` had
no index on `game_id`, so each of those lookups was a sequential scan over
~9.8M rows. The foreign key does not create one; Postgres only indexes the
referenced side.

`shot_attempts.goalie_id` is indexed for the goalie feature extractors,
which slice the shot table by goalie across a date range.

Revision ID: c4e1a9b7d302
Revises: 8ad0fa8aa636
Create Date: 2026-08-02

"""
from typing import Sequence, Union

from alembic import op

revision: str = "c4e1a9b7d302"
down_revision: Union[str, Sequence[str], None] = "8ad0fa8aa636"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_player_shifts_game_id", "player_shifts", ["game_id"], unique=False
    )
    op.create_index(
        "ix_player_shifts_player_game", "player_shifts",
        ["player_id", "game_id"], unique=False,
    )
    op.create_index(
        "ix_shot_attempts_goalie_id", "shot_attempts", ["goalie_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_shot_attempts_goalie_id", table_name="shot_attempts")
    op.drop_index("ix_player_shifts_player_game", table_name="player_shifts")
    op.drop_index("ix_player_shifts_game_id", table_name="player_shifts")

"""add yahoo status and selected position to team rosters

Revision ID: 953949197b71
Revises: 7e206a9c3871
Create Date: 2026-08-03 13:21:09.407611

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '953949197b71'
down_revision: Union[str, Sequence[str], None] = '7e206a9c3871'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the two Yahoo roster fields the optimizer needs for IR decisions.

    Autogenerate also proposed dropping `ix_player_shifts_game_id`,
    `ix_player_shifts_player_game`, and `ix_shot_attempts_goalie_id`. Those
    indexes exist in the database but not in the models, so autogenerate reads
    them as drift. They are deliberate performance indexes and dropping them
    is not part of this change, so they are left alone.
    """
    op.add_column('team_rosters', sa.Column('yahoo_status', sa.String(length=16), nullable=True))
    op.add_column(
        'team_rosters', sa.Column('selected_position', sa.String(length=8), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('team_rosters', 'selected_position')
    op.drop_column('team_rosters', 'yahoo_status')

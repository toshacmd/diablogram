"""add account telegram profile fields

Revision ID: 15c0d9e61190
Revises: 8718f3815e9b
Create Date: 2026-07-25 20:28:34.858859

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '15c0d9e61190'
down_revision: Union[str, Sequence[str], None] = '8718f3815e9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('accounts', sa.Column('tg_user_id', sa.BigInteger(), nullable=True))
    op.add_column('accounts', sa.Column('tg_username', sa.String(length=64), nullable=True))
    op.add_column('accounts', sa.Column('tg_first_name', sa.String(length=255), nullable=True))
    op.add_column('accounts', sa.Column('tg_last_name', sa.String(length=255), nullable=True))
    op.add_column('accounts', sa.Column('tg_bio', sa.String(length=70), nullable=True))
    op.add_column('accounts', sa.Column('tg_synced_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('accounts', 'tg_synced_at')
    op.drop_column('accounts', 'tg_bio')
    op.drop_column('accounts', 'tg_last_name')
    op.drop_column('accounts', 'tg_first_name')
    op.drop_column('accounts', 'tg_username')
    op.drop_column('accounts', 'tg_user_id')

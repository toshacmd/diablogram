"""background channel join status

Revision ID: d5b2cf8da4ce
Revises: 39835f938151
Create Date: 2026-07-26 10:57:20.708189

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd5b2cf8da4ce'
down_revision: Union[str, Sequence[str], None] = '39835f938151'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Unlike op.create_table, op.add_column does not auto-create a new
    # Postgres enum type for its column — has to be done explicitly first,
    # or ADD COLUMN fails with "type joinstatus does not exist". Guarded to
    # postgres only since sqlite (local dev) has no CREATE TYPE at all.
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("CREATE TYPE joinstatus AS ENUM ('PENDING', 'JOINED', 'PENDING_APPROVAL', 'FAILED')")

    # Existing assignments were already joined synchronously by the old code
    # path — default them to 'joined' so only genuinely new assignments (added
    # after this migration) start out 'pending' and get picked up by the
    # worker's background join step.
    join_status_enum = postgresql.ENUM(
        'PENDING', 'JOINED', 'PENDING_APPROVAL', 'FAILED', name='joinstatus', create_type=False
    )
    op.add_column(
        'account_channel_assignments',
        sa.Column('join_status', join_status_enum, nullable=False, server_default='JOINED'),
    )
    op.add_column('account_channel_assignments', sa.Column('join_error', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('account_channel_assignments', 'join_error')
    op.drop_column('account_channel_assignments', 'join_status')
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('DROP TYPE IF EXISTS joinstatus')

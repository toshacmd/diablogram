"""background channel join status

Revision ID: d5b2cf8da4ce
Revises: 39835f938151
Create Date: 2026-07-26 10:57:20.708189

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5b2cf8da4ce'
down_revision: Union[str, Sequence[str], None] = '39835f938151'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Existing assignments were already joined synchronously by the old code
    # path — default them to 'joined' so only genuinely new assignments (added
    # after this migration) start out 'pending' and get picked up by the
    # worker's background join step.
    op.add_column(
        'account_channel_assignments',
        sa.Column(
            'join_status',
            sa.Enum('PENDING', 'JOINED', 'PENDING_APPROVAL', 'FAILED', name='joinstatus'),
            nullable=False,
            server_default='JOINED',
        ),
    )
    op.add_column('account_channel_assignments', sa.Column('join_error', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('account_channel_assignments', 'join_error')
    op.drop_column('account_channel_assignments', 'join_status')
    op.execute('DROP TYPE IF EXISTS joinstatus')

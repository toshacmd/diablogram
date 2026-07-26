"""add proxy catalog and bulk profile tasks

Revision ID: b7c41a92e310
Revises: d5b2cf8da4ce
Create Date: 2026-07-26 15:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c41a92e310'
down_revision: Union[str, Sequence[str], None] = 'd5b2cf8da4ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # New enum types arrive via op.create_table, which (unlike op.add_column,
    # see the joinstatus migration) creates them automatically on Postgres.
    op.create_table(
        'proxies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=True),
        sa.Column('proxy_type', sa.String(length=20), nullable=False),
        sa.Column('proxy_host', sa.String(length=255), nullable=False),
        sa.Column('proxy_port', sa.Integer(), nullable=False),
        sa.Column('proxy_username', sa.String(length=255), nullable=True),
        sa.Column('proxy_password_enc', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'profile_tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Enum('AVATAR', 'STORY', 'BIO', name='profiletaskkind'), nullable=False),
        sa.Column('media_path', sa.String(length=255), nullable=True),
        sa.Column('media_filename', sa.String(length=255), nullable=True),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'profile_task_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'DONE', 'FAILED', name='profiletaskitemstatus'), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['profile_tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    # Plain integer FK column — no enum involved (see the joinstatus lesson).
    # batch_alter_table because SQLite can't ALTER in a foreign key: there it
    # recreates the table, on Postgres it emits ordinary ALTER statements.
    with op.batch_alter_table('accounts') as batch_op:
        batch_op.add_column(sa.Column('proxy_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_accounts_proxy_id', 'proxies', ['proxy_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('accounts') as batch_op:
        batch_op.drop_constraint('fk_accounts_proxy_id', type_='foreignkey')
        batch_op.drop_column('proxy_id')
    op.drop_table('profile_task_items')
    op.drop_table('profile_tasks')
    op.drop_table('proxies')
    sa.Enum(name='profiletaskkind').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='profiletaskitemstatus').drop(op.get_bind(), checkfirst=True)

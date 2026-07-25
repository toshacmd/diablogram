"""add scrape accounts and parse runs

Revision ID: 39835f938151
Revises: 15c0d9e61190
Create Date: 2026-07-26 00:00:22.885961

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '39835f938151'
down_revision: Union[str, Sequence[str], None] = '15c0d9e61190'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # accountstatus enum type already exists (created by the initial schema
    # migration for accounts.status) — reuse it, don't CREATE TYPE again.
    account_status_enum = postgresql.ENUM(
        'ACTIVE', 'LIMITED', 'BANNED', 'DISABLED', name='accountstatus', create_type=False
    )

    op.create_table('scrape_accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=100), nullable=True),
    sa.Column('session_string_enc', sa.Text(), nullable=False),
    sa.Column('proxy_type', sa.String(length=20), nullable=True),
    sa.Column('proxy_host', sa.String(length=255), nullable=True),
    sa.Column('proxy_port', sa.Integer(), nullable=True),
    sa.Column('proxy_username', sa.String(length=255), nullable=True),
    sa.Column('proxy_password_enc', sa.Text(), nullable=True),
    sa.Column('is_premium', sa.Boolean(), nullable=False),
    sa.Column('status', account_status_enum, nullable=False),
    sa.Column('status_note', sa.String(length=500), nullable=True),
    sa.Column('limited_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('parse_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('keywords', sa.Text(), nullable=False),
    sa.Column('min_subscribers', sa.Integer(), nullable=False),
    sa.Column('max_inactive_days', sa.Integer(), nullable=False),
    sa.Column('depth', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', name='parserunstatus'), nullable=False),
    sa.Column('status_note', sa.Text(), nullable=True),
    sa.Column('channels_found', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('parsed_channels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('tg_channel_id', sa.BigInteger(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('subscriber_count', sa.Integer(), nullable=False),
    sa.Column('found_via', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['parse_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'tg_channel_id', name='uq_parsed_channel_run')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('parsed_channels')
    op.drop_table('parse_runs')
    op.drop_table('scrape_accounts')
    # accountstatus enum type is still used by accounts.status — not dropped here.

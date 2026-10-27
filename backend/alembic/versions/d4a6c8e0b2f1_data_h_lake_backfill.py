"""DATA-H: the index-history backfill's resume points and the closing-auction bar flags

Revision ID: d4a6c8e0b2f1
Revises: 7e2a4c6b8d10 (TM-2, the resting PAPER order book)
Create Date: 2026-10-24 18:00:00

- lake_backfill_windows: one row per request window of the owner's index-history backfill (DH1; the resume point).
- md_bar_flags: bars no structure or zone is built from (a bar after continuous trading, a Daily extreme set only in the
  closing auction) and the official close of a session (DH3).
Two new tables only; nothing existing is altered; no backfill.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4a6c8e0b2f1'
down_revision: Union[str, Sequence[str], None] = '7e2a4c6b8d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("two new tables only: the previous release never reads or writes lake_backfill_windows or md_bar_flags, "
                        "so it runs unchanged on the upgraded schema; the downgrade drops both tables and their indexes")


def upgrade() -> None:
    op.create_table(
        'lake_backfill_windows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('instrument_key', sa.String(length=64), nullable=False),
        sa.Column('timeframe', sa.String(length=8), nullable=False),
        sa.Column('window_start', sa.Date(), nullable=False),
        sa.Column('window_end', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('bars', sa.Integer(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'user_id', 'instrument_key', 'timeframe', 'window_start', name='uq_lake_backfill_window'),
    )
    op.create_index(op.f('ix_lake_backfill_windows_tenant_id'), 'lake_backfill_windows', ['tenant_id'], unique=False)
    op.create_table(
        'md_bar_flags',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('instrument_key', sa.String(length=64), nullable=False),
        sa.Column('timeframe', sa.String(length=8), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('value', sa.Numeric(18, 4, asdecimal=False), nullable=True),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_md_bar_flags_lookup', 'md_bar_flags', ['tenant_id', 'instrument_key', 'timeframe', 'ts'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_md_bar_flags_lookup', table_name='md_bar_flags')
    op.drop_table('md_bar_flags')
    op.drop_index(op.f('ix_lake_backfill_windows_tenant_id'), table_name='lake_backfill_windows')
    op.drop_table('lake_backfill_windows')

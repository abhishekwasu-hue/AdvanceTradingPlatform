"""HB2: history_intraday_days - the resume point and progress of the owner's 1-minute capture / backfill (flag
history_intraday, off by default). The bars go to HB0's existing md_tenant_candles; no other table changes.

Revision ID: f3b5d7a9c1e2
Revises: e8a1c4d2f9b7 (U8 EV1/EV2 screen_evidence, main's head after Z11)
Create Date: 2026-10-12 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f3b5d7a9c1e2'
down_revision: Union[str, Sequence[str], None] = 'e8a1c4d2f9b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - one new table that no earlier release reads or writes, so the previous release runs unchanged
# on this schema (a code-only rollback is fine; the table is simply ignored).
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.create_table(
        'history_intraday_days',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('instrument_key', sa.String(length=64), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='pending'),
        sa.Column('source', sa.String(length=30), nullable=True),
        sa.Column('credential_id', sa.Integer(), sa.ForeignKey('broker_credentials.id', ondelete='SET NULL'), nullable=True),
        sa.Column('endpoint', sa.String(length=16), nullable=True),
        sa.Column('bars', sa.Integer(), nullable=True),
        sa.Column('inserted', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('corrected', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('skipped', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('dropped_forming', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('quality_events', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('first_ts', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_ts', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'instrument_key', 'trade_date', name='uq_history_intraday_day'),
    )
    op.create_index('ix_history_intraday_days_tenant_id', 'history_intraday_days', ['tenant_id'])
    op.create_index('ix_history_intraday_days_trade_date', 'history_intraday_days', ['trade_date'])
    op.create_index('ix_history_intraday_days_status', 'history_intraday_days', ['status'])


def downgrade() -> None:
    op.drop_index('ix_history_intraday_days_status', table_name='history_intraday_days')
    op.drop_index('ix_history_intraday_days_trade_date', table_name='history_intraday_days')
    op.drop_index('ix_history_intraday_days_tenant_id', table_name='history_intraday_days')
    op.drop_table('history_intraday_days')

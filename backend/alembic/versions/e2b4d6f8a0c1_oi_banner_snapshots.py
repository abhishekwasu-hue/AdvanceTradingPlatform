"""OI Banner O2: oi_snapshots, strike_oi_snapshots, oi_day_baselines, oi_banner_settings

Revision ID: e2b4d6f8a0c1
Revises: a1c3e5f7b9d2
Create Date: 2026-10-11 01:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e2b4d6f8a0c1'
down_revision: Union[str, Sequence[str], None] = 'a1c3e5f7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'oi_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('underlying', sa.String(length=30), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('slot_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expiry', sa.Date(), nullable=True),
        sa.Column('underlying_price', sa.Float(), nullable=True),
        sa.Column('strikes', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.UniqueConstraint('underlying', 'slot_start', name='uq_oi_snapshot_slot'),
    )
    op.create_index('ix_oi_snapshots_day', 'oi_snapshots', ['underlying', 'trade_date'])
    op.create_table(
        'strike_oi_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('snapshot_id', sa.Integer(), sa.ForeignKey('oi_snapshots.id', ondelete='CASCADE'), nullable=False),
        sa.Column('strike', sa.Float(), nullable=False),
        sa.Column('call_oi', sa.Float(), nullable=True),
        sa.Column('put_oi', sa.Float(), nullable=True),
        sa.Column('call_ltp', sa.Float(), nullable=True),
        sa.Column('put_ltp', sa.Float(), nullable=True),
        sa.Column('call_iv', sa.Float(), nullable=True),
        sa.Column('put_iv', sa.Float(), nullable=True),
        sa.UniqueConstraint('snapshot_id', 'strike', name='uq_strike_oi_snapshot'),
    )
    op.create_index('ix_strike_oi_snapshots_snapshot_id', 'strike_oi_snapshots', ['snapshot_id'])
    op.create_table(
        'oi_day_baselines',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('underlying', sa.String(length=30), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('strike', sa.Float(), nullable=False),
        sa.Column('call_oi', sa.Float(), nullable=True),
        sa.Column('put_oi', sa.Float(), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('underlying', 'trade_date', 'strike', name='uq_oi_day_baseline'),
    )
    op.create_table(
        'oi_banner_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('underlying', sa.String(length=30), nullable=False),
        sa.Column('exchange', sa.String(length=10), nullable=False, server_default='NSE'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('overrides', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'underlying', name='uq_oi_banner_setting'),
    )
    op.create_index('ix_oi_banner_settings_tenant_id', 'oi_banner_settings', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_oi_banner_settings_tenant_id', table_name='oi_banner_settings')
    op.drop_table('oi_banner_settings')
    op.drop_table('oi_day_baselines')
    op.drop_index('ix_strike_oi_snapshots_snapshot_id', table_name='strike_oi_snapshots')
    op.drop_table('strike_oi_snapshots')
    op.drop_index('ix_oi_snapshots_day', table_name='oi_snapshots')
    op.drop_table('oi_snapshots')

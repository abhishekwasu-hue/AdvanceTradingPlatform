"""Z7a-2: market_zone_settings (tenant overrides per lake instrument), market_zone_runs + market_zones (as-of zone
snapshots with lifecycle state and level memory, computed from the shared lake bars by the flag-gated nightly job).

Revision ID: b476873844dd
Revises: 612f572d7ad2
Create Date: 2026-10-11 21:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b476873844dd'
down_revision: Union[str, Sequence[str], None] = '612f572d7ad2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Creates three new tables (and their indexes) only; downgrade drops them - nothing existing is altered.
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.create_table(
        'market_zone_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('instrument_key', sa.String(length=64), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('overrides', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'instrument_key', name='uq_market_zone_setting'),
    )
    op.create_index('ix_market_zone_settings_tenant_id', 'market_zone_settings', ['tenant_id'])
    op.create_table(
        'market_zone_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('scope', sa.String(length=24), nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('instrument_key', sa.String(length=64), nullable=False),
        sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('settings_hash', sa.String(length=64), nullable=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('bars', sa.Text(), nullable=False),
        sa.Column('zone_count', sa.Integer(), nullable=False),
        sa.Column('previous_run_id', sa.Integer(), nullable=True),
        sa.Column('source_last_ts', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source_ingested_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('scope', 'instrument_key', 'as_of', name='uq_market_zone_run'),
    )
    op.create_index('ix_market_zone_runs_tenant_id', 'market_zone_runs', ['tenant_id'])
    op.create_table(
        'market_zones',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('market_zone_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('zone_key', sa.String(length=200), nullable=False),
        sa.Column('zone_type', sa.String(length=40), nullable=False),
        sa.Column('timeframe', sa.String(length=8), nullable=False),
        sa.Column('zone_low', sa.Float(), nullable=False),
        sa.Column('zone_high', sa.Float(), nullable=False),
        sa.Column('strength', sa.Float(), nullable=True),
        sa.Column('role', sa.String(length=20), nullable=True),
        sa.Column('origin_role', sa.String(length=12), nullable=False),
        sa.Column('state', sa.String(length=12), nullable=False),
        sa.Column('formed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_touch_event', sa.String(length=12), nullable=True),
        sa.Column('first_touch_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('touches', sa.Integer(), nullable=False),
        sa.Column('flips', sa.Integer(), nullable=False),
        sa.Column('last_event', sa.String(length=20), nullable=True),
        sa.Column('last_event_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('structure_level', sa.Float(), nullable=True),
        sa.Column('invalidated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('remembered', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('tracker', sa.Text(), nullable=False),
    )
    op.create_index('ix_market_zones_run_id', 'market_zones', ['run_id'])


def downgrade() -> None:
    op.drop_index('ix_market_zones_run_id', table_name='market_zones')
    op.drop_table('market_zones')
    op.drop_index('ix_market_zone_runs_tenant_id', table_name='market_zone_runs')
    op.drop_table('market_zone_runs')
    op.drop_index('ix_market_zone_settings_tenant_id', table_name='market_zone_settings')
    op.drop_table('market_zone_settings')

"""OI Banner O4: oi_banner_states, oi_alert_log, oi_banner_settings.snoozed_until, notifications.metadata_json

Revision ID: e2b4d6f8a0c2
Revises: e9a1c3d5f7b9
Create Date: 2026-10-11 01:40:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e2b4d6f8a0c2'
down_revision: Union[str, Sequence[str], None] = 'e9a1c3d5f7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('notifications', sa.Column('metadata_json', sa.Text(), nullable=True))
    op.add_column('oi_banner_settings', sa.Column('snoozed_until', sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        'oi_banner_states',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('underlying', sa.String(length=30), nullable=False),
        sa.Column('slot_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('stable_direction', sa.String(length=10), nullable=False),
        sa.Column('stable_strength', sa.String(length=12), nullable=True),
        sa.Column('pcr_band', sa.String(length=16), nullable=True),
        sa.Column('max_pain', sa.Float(), nullable=True),
        sa.Column('max_pain_ref', sa.Float(), nullable=True),
        sa.Column('dte', sa.Integer(), nullable=True),
        sa.Column('wall', sa.String(length=40), nullable=True),
        sa.Column('message', sa.String(length=300), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'underlying', 'slot_start', name='uq_oi_banner_state'),
    )
    op.create_index('ix_oi_banner_states_tenant_id', 'oi_banner_states', ['tenant_id'])
    op.create_table(
        'oi_alert_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('underlying', sa.String(length=30), nullable=False),
        sa.Column('alert_type', sa.String(length=30), nullable=False),
        sa.Column('old_state', sa.String(length=40), nullable=True),
        sa.Column('new_state', sa.String(length=40), nullable=False),
        sa.Column('slot_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('dedupe_key', sa.String(length=200), nullable=False, unique=True),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('notification_id', sa.Integer(), sa.ForeignKey('notifications.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_oi_alert_log_tenant_id', 'oi_alert_log', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_oi_alert_log_tenant_id', table_name='oi_alert_log')
    op.drop_table('oi_alert_log')
    op.drop_index('ix_oi_banner_states_tenant_id', table_name='oi_banner_states')
    op.drop_table('oi_banner_states')
    op.drop_column('oi_banner_settings', 'snoozed_until')
    op.drop_column('notifications', 'metadata_json')

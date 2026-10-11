"""S3a: Notification Service model - alert_rules, alert_events, notification_policies; alert_deliveries gains
priority, group_id, digest_bucket, reason_code

Revision ID: e9a1c3d5f7b9
Revises: c5e7a9b1d3f5
Create Date: 2026-10-11 05:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e9a1c3d5f7b9'
down_revision: Union[str, Sequence[str], None] = 'c5e7a9b1d3f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alert_deliveries', sa.Column('priority', sa.String(length=10), nullable=True))
    op.add_column('alert_deliveries', sa.Column('group_id', sa.String(length=64), nullable=True))
    op.add_column('alert_deliveries', sa.Column('digest_bucket', sa.String(length=32), nullable=True))
    op.add_column('alert_deliveries', sa.Column('reason_code', sa.String(length=40), nullable=True))
    op.create_index(op.f('ix_alert_deliveries_group_id'), 'alert_deliveries', ['group_id'], unique=False)
    op.create_table(
        'alert_rules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('screen_id', sa.Integer(), nullable=True),
        sa.Column('symbol', sa.String(length=40), nullable=True),
        sa.Column('condition_text', sa.Text(), nullable=True),
        sa.Column('base_tf', sa.String(length=4), nullable=False),
        sa.Column('priority', sa.String(length=10), nullable=False),
        sa.Column('cooldown_minutes', sa.Integer(), nullable=False),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('digest_every', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['screen_id'], ['screens.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_alert_rules_tenant_id'), 'alert_rules', ['tenant_id'], unique=False)
    op.create_table(
        'alert_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('rule_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=40), nullable=False),
        sa.Column('condition_hash', sa.String(length=64), nullable=False),
        sa.Column('bar_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('idem_key', sa.String(length=64), nullable=False),
        sa.Column('priority', sa.String(length=10), nullable=False),
        sa.Column('values_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('reason_code', sa.String(length=40), nullable=True),
        sa.Column('group_id', sa.String(length=64), nullable=True),
        sa.Column('notification_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['notification_id'], ['notifications.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['rule_id'], ['alert_rules.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idem_key'),
    )
    op.create_index(op.f('ix_alert_events_tenant_id'), 'alert_events', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_alert_events_rule_id'), 'alert_events', ['rule_id'], unique=False)
    op.create_index(op.f('ix_alert_events_status'), 'alert_events', ['status'], unique=False)
    op.create_index(op.f('ix_alert_events_created_at'), 'alert_events', ['created_at'], unique=False)
    op.create_table(
        'notification_policies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('timezone', sa.String(length=40), nullable=False),
        sa.Column('quiet_start', sa.String(length=5), nullable=True),
        sa.Column('quiet_end', sa.String(length=5), nullable=True),
        sa.Column('max_per_hour', sa.Integer(), nullable=False),
        sa.Column('group_window_seconds', sa.Integer(), nullable=False),
        sa.Column('eod_digest_time', sa.String(length=5), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id'),
    )


def downgrade() -> None:
    op.drop_table('notification_policies')
    op.drop_index(op.f('ix_alert_events_created_at'), table_name='alert_events')
    op.drop_index(op.f('ix_alert_events_status'), table_name='alert_events')
    op.drop_index(op.f('ix_alert_events_rule_id'), table_name='alert_events')
    op.drop_index(op.f('ix_alert_events_tenant_id'), table_name='alert_events')
    op.drop_table('alert_events')
    op.drop_index(op.f('ix_alert_rules_tenant_id'), table_name='alert_rules')
    op.drop_table('alert_rules')
    op.drop_index(op.f('ix_alert_deliveries_group_id'), table_name='alert_deliveries')
    op.drop_column('alert_deliveries', 'reason_code')
    op.drop_column('alert_deliveries', 'digest_bucket')
    op.drop_column('alert_deliveries', 'group_id')
    op.drop_column('alert_deliveries', 'priority')

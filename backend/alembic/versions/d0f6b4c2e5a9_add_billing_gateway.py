"""billing gateway: checkout url, gateway plan map, webhook event log (Phase K1b)

Revision ID: d0f6b4c2e5a9
Revises: c9e5a3b1d4a8
Create Date: 2026-09-27 01:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd0f6b4c2e5a9'
down_revision: Union[str, Sequence[str], None] = 'c9e5a3b1d4a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('subscriptions') as batch:
        batch.add_column(sa.Column('checkout_url', sa.String(length=500), nullable=True))
    op.create_table(
        'billing_gateway_plans',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('plan_id', sa.String(length=50), nullable=False),
        sa.Column('billing_cycle', sa.String(length=10), nullable=False),
        sa.Column('gateway_plan_id', sa.String(length=100), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('provider', 'plan_id', 'billing_cycle', name='uq_gateway_plan'),
    )
    op.create_table(
        'billing_webhook_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('event_id', sa.String(length=100), nullable=False),
        sa.Column('event_type', sa.String(length=60), nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='SET NULL'), nullable=True),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('result', sa.String(length=300), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_billing_webhook_events_event_id', 'billing_webhook_events', ['event_id'], unique=True)
    op.create_index('ix_billing_webhook_events_received_at', 'billing_webhook_events', ['received_at'])


def downgrade() -> None:
    op.drop_index('ix_billing_webhook_events_received_at', table_name='billing_webhook_events')
    op.drop_index('ix_billing_webhook_events_event_id', table_name='billing_webhook_events')
    op.drop_table('billing_webhook_events')
    op.drop_table('billing_gateway_plans')
    with op.batch_alter_table('subscriptions') as batch:
        batch.drop_column('checkout_url')

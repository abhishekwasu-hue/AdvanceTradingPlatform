"""billing (subscriptions, transactions, usage), marketplace, api keys, tenant status reason (Phase K)

Revision ID: b8d4f2a0c397
Revises: a7c3e1f9b286
Create Date: 2026-09-26 23:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b8d4f2a0c397'
down_revision: Union[str, Sequence[str], None] = 'a7c3e1f9b286'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenants') as batch:
        batch.add_column(sa.Column('status_reason', sa.String(length=200), nullable=True))
    op.create_table(
        'subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_id', sa.String(length=50), nullable=False),
        sa.Column('billing_cycle', sa.String(length=10), nullable=False, server_default='MONTHLY'),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='TRIALING'),
        sa.Column('provider', sa.String(length=30), nullable=False, server_default='manual'),
        sa.Column('provider_ref', sa.String(length=200), nullable=True),
        sa.Column('current_period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('trial_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('grace_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_subscriptions_tenant_id', 'subscriptions', ['tenant_id'], unique=True)
    op.create_table(
        'billing_transactions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('subscription_id', sa.Integer(), sa.ForeignKey('subscriptions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('description', sa.String(length=300), nullable=False),
        sa.Column('provider_ref', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_billing_transactions_tenant_id', 'billing_transactions', ['tenant_id'])
    op.create_index('ix_billing_transactions_created_at', 'billing_transactions', ['created_at'])
    op.create_table(
        'usage_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('metric', sa.String(length=40), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False, server_default='1'),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False, server_default='api'),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_usage_records_tenant_id', 'usage_records', ['tenant_id'])
    op.create_index('ix_usage_records_metric', 'usage_records', ['metric'])
    op.create_index('ix_usage_records_period_start', 'usage_records', ['period_start'])
    op.create_table(
        'marketplace_listings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('custom_strategy_id', sa.Integer(), sa.ForeignKey('custom_strategies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('config_json', sa.Text(), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('methodology', sa.Text(), nullable=True),
        sa.Column('backtest_run_id', sa.Integer(), sa.ForeignKey('backtest_runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('performance_json', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='DRAFT'),
        sa.Column('review_note', sa.String(length=500), nullable=True),
        sa.Column('subscriber_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_marketplace_listings_tenant_id', 'marketplace_listings', ['tenant_id'])
    op.create_table(
        'marketplace_subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('listing_id', sa.Integer(), sa.ForeignKey('marketplace_listings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('custom_strategy_id', sa.Integer(), sa.ForeignKey('custom_strategies.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='ACTIVE'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('listing_id', 'tenant_id', name='uq_marketplace_subscriber'),
    )
    op.create_index('ix_marketplace_subscriptions_listing_id', 'marketplace_subscriptions', ['listing_id'])
    op.create_index('ix_marketplace_subscriptions_tenant_id', 'marketplace_subscriptions', ['tenant_id'])
    op.create_table(
        'api_keys',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('key_prefix', sa.String(length=12), nullable=False),
        sa.Column('key_hash', sa.String(length=64), nullable=False, unique=True),
        sa.Column('scopes', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('rate_limit_per_minute', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_api_keys_tenant_id', 'api_keys', ['tenant_id'])
    op.create_index('ix_api_keys_key_prefix', 'api_keys', ['key_prefix'])


def downgrade() -> None:
    for table, indexes in (
        ('api_keys', ['ix_api_keys_key_prefix', 'ix_api_keys_tenant_id']),
        ('marketplace_subscriptions', ['ix_marketplace_subscriptions_tenant_id', 'ix_marketplace_subscriptions_listing_id']),
        ('marketplace_listings', ['ix_marketplace_listings_tenant_id']),
        ('usage_records', ['ix_usage_records_period_start', 'ix_usage_records_metric', 'ix_usage_records_tenant_id']),
        ('billing_transactions', ['ix_billing_transactions_created_at', 'ix_billing_transactions_tenant_id']),
        ('subscriptions', ['ix_subscriptions_tenant_id']),
    ):
        for index in indexes:
            op.drop_index(index, table_name=table)
        op.drop_table(table)
    with op.batch_alter_table('tenants') as batch:
        batch.drop_column('status_reason')

"""risk hierarchy (risk_limits, risk_events) and broker accounts (Phase I)

Revision ID: f6b2d0e8a175
Revises: e5a1c9d7f064
Create Date: 2026-09-26 21:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f6b2d0e8a175'
down_revision: Union[str, Sequence[str], None] = 'e5a1c9d7f064'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'risk_limits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('scope', sa.String(length=12), nullable=False),
        sa.Column('scope_id', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('limit_type', sa.String(length=40), nullable=False),
        sa.Column('limit_value', sa.Float(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'scope', 'scope_id', 'limit_type', name='uq_risk_limit_scope'),
    )
    op.create_index('ix_risk_limits_tenant_id', 'risk_limits', ['tenant_id'])
    op.create_table(
        'risk_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=True),
        sa.Column('strategy_id', sa.String(length=100), nullable=True),
        sa.Column('symbol', sa.String(length=50), nullable=True),
        sa.Column('rule_id', sa.Integer(), nullable=True),
        sa.Column('rule_type', sa.String(length=40), nullable=False),
        sa.Column('scope', sa.String(length=12), nullable=False),
        sa.Column('current_value', sa.Float(), nullable=False),
        sa.Column('limit_value', sa.Float(), nullable=False),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('action', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('reason', sa.String(length=300), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
    )
    op.create_index('ix_risk_events_tenant_id', 'risk_events', ['tenant_id'])
    op.create_index('ix_risk_events_created_at', 'risk_events', ['created_at'])
    op.create_index('ix_risk_events_strategy_id', 'risk_events', ['strategy_id'])

    with op.batch_alter_table('broker_credentials') as batch:
        batch.add_column(sa.Column('account_label', sa.String(length=50), nullable=False, server_default='primary'))
        batch.drop_constraint('uq_tenant_broker', type_='unique')
        batch.create_unique_constraint('uq_tenant_broker_label', ['tenant_id', 'broker_name', 'account_label'])

    op.create_table(
        'broker_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('credential_id', sa.Integer(), sa.ForeignKey('broker_credentials.id', ondelete='SET NULL'), nullable=True),
        sa.Column('broker_name', sa.String(length=50), nullable=False),
        sa.Column('account_label', sa.String(length=50), nullable=False, server_default='primary'),
        sa.Column('broker_account_identifier', sa.String(length=100), nullable=True),
        sa.Column('display_name', sa.String(length=100), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='ACTIVE'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('available_balance', sa.Float(), nullable=True),
        sa.Column('used_margin', sa.Float(), nullable=True),
        sa.Column('realized_pnl', sa.Float(), nullable=True),
        sa.Column('unrealized_pnl', sa.Float(), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_error', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'broker_name', 'account_label', name='uq_broker_account_label'),
    )
    op.create_index('ix_broker_accounts_tenant_id', 'broker_accounts', ['tenant_id'])
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.add_column(sa.Column('broker_account_id', sa.Integer(), sa.ForeignKey('broker_accounts.id', ondelete='SET NULL'), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.drop_column('broker_account_id')
    op.drop_index('ix_broker_accounts_tenant_id', table_name='broker_accounts')
    op.drop_table('broker_accounts')
    with op.batch_alter_table('broker_credentials') as batch:
        batch.drop_constraint('uq_tenant_broker_label', type_='unique')
        batch.create_unique_constraint('uq_tenant_broker', ['tenant_id', 'broker_name'])
        batch.drop_column('account_label')
    op.drop_index('ix_risk_events_strategy_id', table_name='risk_events')
    op.drop_index('ix_risk_events_created_at', table_name='risk_events')
    op.drop_index('ix_risk_events_tenant_id', table_name='risk_events')
    op.drop_table('risk_events')
    op.drop_index('ix_risk_limits_tenant_id', table_name='risk_limits')
    op.drop_table('risk_limits')

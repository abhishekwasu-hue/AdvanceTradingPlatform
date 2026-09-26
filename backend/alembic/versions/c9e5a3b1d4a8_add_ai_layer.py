"""AI layer: provider configs, strategy drafts with review gate, monitoring actions, regime filter (Phase L)

Revision ID: c9e5a3b1d4a8
Revises: b8d4f2a0c397
Create Date: 2026-09-27 02:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c9e5a3b1d4a8'
down_revision: Union[str, Sequence[str], None] = 'b8d4f2a0c397'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('custom_strategies') as batch:
        batch.add_column(sa.Column('origin', sa.String(length=40), nullable=False, server_default='user'))
        batch.add_column(sa.Column('ai_approved_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.add_column(sa.Column('regime_filter', sa.String(length=80), nullable=True))
    op.create_table(
        'ai_provider_configs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('model', sa.String(length=80), nullable=False, server_default=''),
        sa.Column('encrypted_api_key', sa.Text(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=300), nullable=True),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_ai_provider_configs_tenant_id', 'ai_provider_configs', ['tenant_id'], unique=True)
    op.create_table(
        'ai_strategy_drafts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('model', sa.String(length=80), nullable=False, server_default=''),
        sa.Column('raw_response', sa.Text(), nullable=True),
        sa.Column('config_json', sa.Text(), nullable=True),
        sa.Column('explanation', sa.Text(), nullable=True),
        sa.Column('warnings_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='DRAFT'),
        sa.Column('backtest_run_id', sa.Integer(), sa.ForeignKey('backtest_runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('custom_strategy_id', sa.Integer(), sa.ForeignKey('custom_strategies.id', ondelete='SET NULL'), nullable=True),
        sa.Column('approved_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_ai_strategy_drafts_tenant_id', 'ai_strategy_drafts', ['tenant_id'])
    op.create_index('ix_ai_strategy_drafts_created_at', 'ai_strategy_drafts', ['created_at'])
    op.create_table(
        'ai_actions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('deployment_id', sa.Integer(), sa.ForeignKey('strategy_deployments.id', ondelete='CASCADE'), nullable=True),
        sa.Column('trade_id', sa.Integer(), sa.ForeignKey('trades.id', ondelete='SET NULL'), nullable=True),
        sa.Column('action', sa.String(length=24), nullable=False),
        sa.Column('rule', sa.String(length=40), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('evidence_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='PROPOSED'),
        sa.Column('decided_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decision_note', sa.String(length=300), nullable=True),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('result', sa.String(length=300), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_ai_actions_tenant_id', 'ai_actions', ['tenant_id'])
    op.create_index('ix_ai_actions_deployment_id', 'ai_actions', ['deployment_id'])
    op.create_index('ix_ai_actions_status', 'ai_actions', ['status'])
    op.create_index('ix_ai_actions_created_at', 'ai_actions', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_ai_actions_created_at', table_name='ai_actions')
    op.drop_index('ix_ai_actions_status', table_name='ai_actions')
    op.drop_index('ix_ai_actions_deployment_id', table_name='ai_actions')
    op.drop_index('ix_ai_actions_tenant_id', table_name='ai_actions')
    op.drop_table('ai_actions')
    op.drop_index('ix_ai_strategy_drafts_created_at', table_name='ai_strategy_drafts')
    op.drop_index('ix_ai_strategy_drafts_tenant_id', table_name='ai_strategy_drafts')
    op.drop_table('ai_strategy_drafts')
    op.drop_index('ix_ai_provider_configs_tenant_id', table_name='ai_provider_configs')
    op.drop_table('ai_provider_configs')
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.drop_column('regime_filter')
    with op.batch_alter_table('custom_strategies') as batch:
        batch.drop_column('ai_approved_by')
        batch.drop_column('origin')

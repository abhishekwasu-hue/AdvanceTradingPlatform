"""N1 E1 (ADR-0014): execution_intents - the execution core's intent queue (flag execution_core, off by default).

Revision ID: a7d3e9c1b5f2
Revises: e8a1c4d2f9b7 (U8 EV1/EV2 screen_evidence)
Create Date: 2026-10-12 22:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7d3e9c1b5f2'
down_revision: Union[str, Sequence[str], None] = 'e8a1c4d2f9b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive (one new table) - the previous release runs on this schema (code-only rollback ok)


def upgrade() -> None:
    op.create_table(
        'execution_intents',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source', sa.String(length=24), nullable=False),
        sa.Column('source_ref', sa.String(length=120), nullable=False),
        sa.Column('idempotency_key', sa.String(length=200), nullable=False),
        sa.Column('requested_mode', sa.String(length=12), nullable=False),
        sa.Column('route', sa.String(length=10), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('symbol', sa.String(length=40), nullable=False),
        sa.Column('exchange', sa.String(length=12), nullable=False),
        sa.Column('direction', sa.String(length=8), nullable=False),
        sa.Column('strategy_id', sa.String(length=80), nullable=False),
        sa.Column('signal_json', sa.Text(), nullable=False),
        sa.Column('evidence_json', sa.Text(), nullable=False),
        sa.Column('reason', sa.String(length=300), nullable=True),
        sa.Column('ai_action_id', sa.Integer(), sa.ForeignKey('ai_actions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('notification_id', sa.Integer(), sa.ForeignKey('notifications.id', ondelete='SET NULL'), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'user_id', 'idempotency_key', name='uq_execution_intents_tenant_user_key'),
    )
    op.create_index('ix_execution_intents_tenant_id', 'execution_intents', ['tenant_id'])
    op.create_index('ix_execution_intents_user_id', 'execution_intents', ['user_id'])
    op.create_index('ix_execution_intents_status', 'execution_intents', ['status'])
    op.create_index('ix_execution_intents_created_at', 'execution_intents', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_execution_intents_created_at', table_name='execution_intents')
    op.drop_index('ix_execution_intents_status', table_name='execution_intents')
    op.drop_index('ix_execution_intents_user_id', table_name='execution_intents')
    op.drop_index('ix_execution_intents_tenant_id', table_name='execution_intents')
    op.drop_table('execution_intents')

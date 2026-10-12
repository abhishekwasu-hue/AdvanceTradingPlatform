"""BYOK-V1: "Vision with your own AI key" - per-user encrypted provider keys, per-user vision preferences and caps, and
the `billed_to_user_key` marker on `llm_calls` (calls the user pays for on their own key; never the plan budget).

Revision ID: d1b0e7a5c3f1
Revises: c7a1d2e3f4b7
Create Date: 2026-10-11 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd1b0e7a5c3f1'
down_revision: Union[str, Sequence[str], None] = 'c7a1d2e3f4b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - the previous release runs on this schema (code-only rollback ok)


def upgrade() -> None:
    op.create_table(
        'user_ai_keys',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', sa.String(20), nullable=False),
        sa.Column('encrypted_key', sa.Text(), nullable=False),
        sa.Column('key_hint', sa.String(16), nullable=False),
        sa.Column('model', sa.String(80), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_tested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_test_ok', sa.Boolean(), nullable=True),
        sa.Column('last_test_error', sa.String(200), nullable=True),
        sa.UniqueConstraint('user_id', 'provider', name='uq_user_ai_keys_user_provider'),
    )
    op.create_index('ix_user_ai_keys_tenant_id', 'user_ai_keys', ['tenant_id'])
    op.create_index('ix_user_ai_keys_user_id', 'user_ai_keys', ['user_id'])
    op.create_table(
        'user_vision_prefs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('provider', sa.String(20), nullable=True),
        sa.Column('daily_cap_usd', sa.Float(), nullable=False, server_default='1'),
        sa.Column('monthly_cap_usd', sa.Float(), nullable=False, server_default='10'),
        sa.Column('audit_proposals', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('audit_scan_alerts', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('audit_daily_review', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('channel_telegram', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('channel_in_app', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_user_vision_prefs_tenant_id', 'user_vision_prefs', ['tenant_id'])
    op.create_index('ix_user_vision_prefs_user_id', 'user_vision_prefs', ['user_id'], unique=True)
    op.add_column('llm_calls', sa.Column('billed_to_user_key', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('llm_calls', 'billed_to_user_key')
    op.drop_index('ix_user_vision_prefs_user_id', table_name='user_vision_prefs')
    op.drop_index('ix_user_vision_prefs_tenant_id', table_name='user_vision_prefs')
    op.drop_table('user_vision_prefs')
    op.drop_index('ix_user_ai_keys_user_id', table_name='user_ai_keys')
    op.drop_index('ix_user_ai_keys_tenant_id', table_name='user_ai_keys')
    op.drop_table('user_ai_keys')

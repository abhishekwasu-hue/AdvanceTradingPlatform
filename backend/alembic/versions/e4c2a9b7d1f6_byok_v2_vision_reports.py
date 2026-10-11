"""BYOK-V2: `vision_reports` - one user's Vision report on one proposal, made with that user's own key and shown to that
user only (status pending/running/ok/skipped/failed, sanitised reason, report text, the chart's SHA-256 - never the image).

Revision ID: e4c2a9b7d1f6
Revises: d4e6f8a0b2c9
Create Date: 2026-10-11 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e4c2a9b7d1f6'
down_revision: Union[str, Sequence[str], None] = 'd4e6f8a0b2c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vision_reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ai_action_id', sa.Integer(), sa.ForeignKey('ai_actions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('provider', sa.String(20), nullable=True),
        sa.Column('model', sa.String(80), nullable=True),
        sa.Column('reason', sa.String(200), nullable=True),
        sa.Column('report_text', sa.Text(), nullable=True),
        sa.Column('image_sha256', sa.String(64), nullable=True),
        sa.Column('cost_usd', sa.Float(), nullable=False, server_default='0'),
        sa.Column('telegram_status', sa.String(10), nullable=False, server_default='none'),
        sa.Column('telegram_error', sa.String(200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('user_id', 'ai_action_id', name='uq_vision_reports_user_action'),
    )
    op.create_index('ix_vision_reports_tenant_id', 'vision_reports', ['tenant_id'])
    op.create_index('ix_vision_reports_user_id', 'vision_reports', ['user_id'])
    op.create_index('ix_vision_reports_ai_action_id', 'vision_reports', ['ai_action_id'])
    op.create_index('ix_vision_reports_status', 'vision_reports', ['status'])


def downgrade() -> None:
    op.drop_index('ix_vision_reports_status', table_name='vision_reports')
    op.drop_index('ix_vision_reports_ai_action_id', table_name='vision_reports')
    op.drop_index('ix_vision_reports_user_id', table_name='vision_reports')
    op.drop_index('ix_vision_reports_tenant_id', table_name='vision_reports')
    op.drop_table('vision_reports')

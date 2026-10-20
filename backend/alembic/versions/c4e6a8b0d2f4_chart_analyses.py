"""Analyze a chart (PR-A): chart_analyses - one uploaded / linked chart image per row and its analysis, per user
(flag chart_analyze, off by default).

Revision ID: c4e6a8b0d2f4
Revises: d9f1b3c5e7a2 (main's head)
Create Date: 2026-10-20 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c4e6a8b0d2f4'
down_revision: Union[str, Sequence[str], None] = 'd9f1b3c5e7a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - one new table that no earlier release reads or writes, so the previous release runs unchanged
# on this schema (a code-only rollback is fine; the table is simply ignored).
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.create_table(
        'chart_analyses',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False, server_default='image_only'),
        sa.Column('source', sa.String(length=10), nullable=False, server_default='upload'),
        sa.Column('source_host', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='ready'),
        sa.Column('image_path', sa.String(length=200), nullable=True),
        sa.Column('image_sha256', sa.String(length=64), nullable=True),
        sa.Column('image_width', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('image_height', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('image_bytes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('error', sa.String(length=300), nullable=True),
        sa.Column('provider', sa.String(length=20), nullable=True),
        sa.Column('model', sa.String(length=80), nullable=True),
        sa.Column('engine_version', sa.String(length=80), nullable=True),
        sa.Column('billed_to_user_key', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('cost_usd', sa.Float(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('analysed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_chart_analyses_tenant_id', 'chart_analyses', ['tenant_id'])
    op.create_index('ix_chart_analyses_user_id', 'chart_analyses', ['user_id'])
    op.create_index('ix_chart_analyses_status', 'chart_analyses', ['status'])
    op.create_index('ix_chart_analyses_created_at', 'chart_analyses', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_chart_analyses_created_at', table_name='chart_analyses')
    op.drop_index('ix_chart_analyses_status', table_name='chart_analyses')
    op.drop_index('ix_chart_analyses_user_id', table_name='chart_analyses')
    op.drop_index('ix_chart_analyses_tenant_id', table_name='chart_analyses')
    op.drop_table('chart_analyses')

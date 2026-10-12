"""CH5: chart_layouts - saved multi-chart workspace layouts per user (flag chart_workspace, off by default).

Revision ID: b8d0f2a4c6e9
Revises: f3b5d7a9c1e2 (HB2 intraday history, main's head after #212 / HB2; was a7d3e9c1b5f2)
Create Date: 2026-10-12 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b8d0f2a4c6e9'
down_revision: Union[str, Sequence[str], None] = 'f3b5d7a9c1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - one new table that no earlier release reads or writes, so the previous release runs unchanged
# on this schema (a code-only rollback is fine; the table is simply ignored).
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.create_table(
        'chart_layouts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('layout_json', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'name', name='uq_chart_layouts_user_name'),
    )
    op.create_index('ix_chart_layouts_tenant_id', 'chart_layouts', ['tenant_id'])
    op.create_index('ix_chart_layouts_user_id', 'chart_layouts', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_chart_layouts_user_id', table_name='chart_layouts')
    op.drop_index('ix_chart_layouts_tenant_id', table_name='chart_layouts')
    op.drop_table('chart_layouts')

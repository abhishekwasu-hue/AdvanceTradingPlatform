"""N1 U4 X1: scan_then_templates - a scan alert rule's "Then" template (flag scan_then, off by default).

Revision ID: b9e4c2d7a1f3
Revises: b8d0f2a4c6e9 (CH5-a chart layouts #215, main's head)
Create Date: 2026-10-12 23:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b9e4c2d7a1f3'
down_revision: Union[str, Sequence[str], None] = 'b8d0f2a4c6e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive (one new table) - the previous release runs on this schema (code-only rollback ok)


def upgrade() -> None:
    op.create_table(
        'scan_then_templates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('rule_id', sa.Integer(), sa.ForeignKey('alert_rules.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('mode', sa.String(length=12), nullable=False),
        sa.Column('confirm_tf', sa.String(length=4), nullable=False),
        sa.Column('gate_settings_json', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_bar_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_status_json', sa.Text(), nullable=True),
        sa.Column('last_problem', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('rule_id', name='uq_scan_then_templates_rule_id'),
    )
    op.create_index('ix_scan_then_templates_tenant_id', 'scan_then_templates', ['tenant_id'])
    op.create_index('ix_scan_then_templates_user_id', 'scan_then_templates', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_scan_then_templates_user_id', table_name='scan_then_templates')
    op.drop_index('ix_scan_then_templates_tenant_id', table_name='scan_then_templates')
    op.drop_table('scan_then_templates')

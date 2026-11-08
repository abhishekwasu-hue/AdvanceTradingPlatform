"""M89: daily_report_sends - the Telegram send queue of the shared daily report (one row per report and user).

Additive: one new table nothing else reads. The previous release ignores it, so a rollback leaves it in place unused.

Revision ID: d6f8a0c2e4b3
Revises: c4e6a8b0d2f1
Create Date: 2026-11-08 14:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'd6f8a0c2e4b3'
down_revision: Union[str, Sequence[str], None] = 'c4e6a8b0d2f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - a new table the previous release never reads


def upgrade() -> None:
    op.create_table(
        'daily_report_sends',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('report_id', sa.Integer(), sa.ForeignKey('shared_daily_reports.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error', sa.String(200), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('report_id', 'user_id', name='uq_daily_report_send'),
    )
    op.create_index('ix_daily_report_sends_report_id', 'daily_report_sends', ['report_id'])
    op.create_index('ix_daily_report_sends_tenant_id', 'daily_report_sends', ['tenant_id'])
    op.create_index('ix_daily_report_sends_user_id', 'daily_report_sends', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_daily_report_sends_user_id', table_name='daily_report_sends')
    op.drop_index('ix_daily_report_sends_tenant_id', table_name='daily_report_sends')
    op.drop_index('ix_daily_report_sends_report_id', table_name='daily_report_sends')
    op.drop_table('daily_report_sends')

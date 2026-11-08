"""M88-R: shared_daily_reports - the shared daily market report (one per trading day, EOD data only).

Additive: one new table nothing else reads. The previous release ignores it, so a rollback leaves it in place unused.

Revision ID: c4e6a8b0d2f1
Revises: d5f7b9c1e3a8
Create Date: 2026-11-08 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'c4e6a8b0d2f1'
down_revision: Union[str, Sequence[str], None] = 'd5f7b9c1e3a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - a new table the previous release never reads


def upgrade() -> None:
    op.create_table(
        'shared_daily_reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('content_json', sa.Text(), nullable=True),
        sa.Column('error', sa.String(500), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('ai_requests', sa.Integer(), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cost_usd', sa.Float(), nullable=False),
        sa.Column('symbols', sa.Integer(), nullable=False),
        sa.Column('run_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('trigger', sa.String(10), nullable=False),
        sa.Column('next_try_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('trade_date'),
    )


def downgrade() -> None:
    op.drop_table('shared_daily_reports')

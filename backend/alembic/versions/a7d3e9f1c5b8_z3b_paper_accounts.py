"""Z3-b / BT3: `paper_accounts` - one PAPER account per user (starting capital, PAPER-scope pause). Balance and P&L are
summed from the user's own PAPER trades, not stored.

Revision ID: a7d3e9f1c5b8
Revises: f3a9c1e7b2d4
Create Date: 2026-10-11 23:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7d3e9f1c5b8'
down_revision: Union[str, Sequence[str], None] = 'f3a9c1e7b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - the previous release runs on this schema (code-only rollback ok)


def upgrade() -> None:
    op.create_table(
        'paper_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('starting_capital', sa.Numeric(18, 2), nullable=False),
        sa.Column('paused', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('paused_reason', sa.String(200), nullable=True),
        sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index('ix_paper_accounts_tenant_id', 'paper_accounts', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_paper_accounts_tenant_id', table_name='paper_accounts')
    op.drop_table('paper_accounts')

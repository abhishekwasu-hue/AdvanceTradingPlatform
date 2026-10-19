"""AMW-2: watchlists - named symbol lists per user (flag watchlists, off by default).

Revision ID: f3b5d7e9a1c2
Revises: e2a4c6e8f0b3 (trades.exit_sent_at, main's head)
Create Date: 2026-10-19 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f3b5d7e9a1c2'
down_revision: Union[str, Sequence[str], None] = 'e2a4c6e8f0b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - one new table that no earlier release reads or writes, so the previous release runs unchanged
# on this schema (a code-only rollback is fine; the table is simply ignored).
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.create_table(
        'watchlists',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('symbols_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'user_id', 'name', name='uq_watchlists_tenant_user_name'),
    )
    op.create_index('ix_watchlists_tenant_id', 'watchlists', ['tenant_id'])
    op.create_index('ix_watchlists_user_id', 'watchlists', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_watchlists_user_id', table_name='watchlists')
    op.drop_index('ix_watchlists_tenant_id', table_name='watchlists')
    op.drop_table('watchlists')

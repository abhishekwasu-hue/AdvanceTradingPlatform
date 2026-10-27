"""M45 MA-3: paper_sub_accounts (simulated PAPER accounts per user) and trades.paper_sub_account_id

Revision ID: f1b3d5e7a9c2
Revises: e2a4c6e8f0b1 (M45 MA-1)
Create Date: 2026-10-27 04:00:00

One new table and one nullable column on trades; no backfill. The table is kept apart from broker_accounts so a
simulated account can never become a LIVE routing candidate.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f1b3d5e7a9c2'
down_revision: Union[str, Sequence[str], None] = 'e2a4c6e8f0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("a new table and one nullable column on trades: the previous release never names them, so it runs "
                        "unchanged on the upgraded schema; the downgrade drops them")


def upgrade() -> None:
    op.create_table(
        'paper_sub_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('label', sa.String(50), nullable=False),
        sa.Column('capital', sa.Numeric(18, 2), nullable=False),
        sa.Column('role', sa.String(10), nullable=True),
        sa.Column('group_name', sa.String(50), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('sizing_mode', sa.String(12), nullable=False, server_default='MULTIPLIER'),
        sa.Column('sizing_value', sa.Float(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'label', name='uq_paper_sub_account_label'),
    )
    op.create_index('ix_paper_sub_accounts_tenant_id', 'paper_sub_accounts', ['tenant_id'])
    op.create_index('ix_paper_sub_accounts_user_id', 'paper_sub_accounts', ['user_id'])
    op.add_column('trades', sa.Column('paper_sub_account_id', sa.Integer(),
                                      sa.ForeignKey('paper_sub_accounts.id', name='fk_trades_paper_sub_account_id',
                                                    ondelete='SET NULL'), nullable=True))


def downgrade() -> None:
    op.drop_column('trades', 'paper_sub_account_id')
    op.drop_index('ix_paper_sub_accounts_user_id', table_name='paper_sub_accounts')
    op.drop_index('ix_paper_sub_accounts_tenant_id', table_name='paper_sub_accounts')
    op.drop_table('paper_sub_accounts')

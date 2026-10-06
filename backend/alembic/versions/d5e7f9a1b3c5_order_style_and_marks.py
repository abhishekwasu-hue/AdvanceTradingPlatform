"""P0.5: deployments.order_style / market_protection_pct (T5), trades.mark_price / mark_time (T6)

Revision ID: d5e7f9a1b3c5
Revises: c4d6e8f0a2b4
Create Date: 2026-10-06 15:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd5e7f9a1b3c5'
down_revision: Union[str, Sequence[str], None] = 'c4d6e8f0a2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch_op:
        batch_op.add_column(sa.Column('order_style', sa.String(length=20), nullable=False, server_default='MARKET'))
        batch_op.add_column(sa.Column('market_protection_pct', sa.Float(), nullable=True))
    with op.batch_alter_table('trades') as batch_op:
        batch_op.add_column(sa.Column('mark_price', sa.Numeric(precision=18, scale=4), nullable=True))
        batch_op.add_column(sa.Column('mark_time', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('trades') as batch_op:
        batch_op.drop_column('mark_time')
        batch_op.drop_column('mark_price')
    with op.batch_alter_table('strategy_deployments') as batch_op:
        batch_op.drop_column('market_protection_pct')
        batch_op.drop_column('order_style')

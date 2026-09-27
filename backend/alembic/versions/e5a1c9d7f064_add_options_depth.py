"""options depth (Phase H): strike filters and multi-leg structures on deployments; leg grouping on trades

Revision ID: e5a1c9d7f064
Revises: d4f0b8c6e953
Create Date: 2026-09-26 20:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e5a1c9d7f064'
down_revision: Union[str, Sequence[str], None] = 'd4f0b8c6e953'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.add_column(sa.Column('strike_filters', sa.Text(), nullable=True))
        batch.add_column(sa.Column('option_strategy', sa.String(length=20), nullable=False, server_default='SINGLE'))
        batch.add_column(sa.Column('spread_width', sa.Integer(), nullable=False, server_default='2'))
        batch.add_column(sa.Column('target_credit_pct', sa.Float(), nullable=True))
        batch.add_column(sa.Column('stop_credit_pct', sa.Float(), nullable=True))
    with op.batch_alter_table('trades') as batch:
        batch.add_column(sa.Column('leg_group_id', sa.String(length=36), nullable=True))
        batch.add_column(sa.Column('leg_role', sa.String(length=6), nullable=True))
        batch.add_column(sa.Column('option_strategy', sa.String(length=20), nullable=True))
        batch.add_column(sa.Column('group_meta', sa.Text(), nullable=True))
        batch.create_index('ix_trades_leg_group_id', ['leg_group_id'])


def downgrade() -> None:
    with op.batch_alter_table('trades') as batch:
        batch.drop_index('ix_trades_leg_group_id')
        for column in ('group_meta', 'option_strategy', 'leg_role', 'leg_group_id'):
            batch.drop_column(column)
    with op.batch_alter_table('strategy_deployments') as batch:
        for column in ('stop_credit_pct', 'target_credit_pct', 'spread_width', 'option_strategy', 'strike_filters'):
            batch.drop_column(column)

"""Phase AS: swing holding on deployments and trades

Revision ID: e4a6c8d0f2b3
Revises: d3f5b7c9e1a2
Create Date: 2026-10-04 14:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e4a6c8d0f2b3'
down_revision: Union[str, Sequence[str], None] = 'd3f5b7c9e1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.add_column(sa.Column('holding', sa.String(length=10), nullable=False, server_default='INTRADAY'))
    with op.batch_alter_table('trades') as batch:
        batch.add_column(sa.Column('holding', sa.String(length=10), nullable=False, server_default='INTRADAY'))


def downgrade() -> None:
    with op.batch_alter_table('trades') as batch:
        batch.drop_column('holding')
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.drop_column('holding')

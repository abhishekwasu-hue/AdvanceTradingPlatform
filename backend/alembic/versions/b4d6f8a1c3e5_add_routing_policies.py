"""Phase T: broker-selection rules - routing policy on deployments and tenants

Revision ID: b4d6f8a1c3e5
Revises: a3c5e7f9b1d2
Create Date: 2026-09-28 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b4d6f8a1c3e5'
down_revision: Union[str, Sequence[str], None] = 'a3c5e7f9b1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.add_column(sa.Column('routing_policy', sa.String(length=20), nullable=True))
        batch.add_column(sa.Column('route_across_brokers', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('last_route', sa.String(length=200), nullable=True))
    with op.batch_alter_table('tenants') as batch:
        batch.add_column(sa.Column('default_routing_policy', sa.String(length=20), nullable=False, server_default='EXPLICIT'))
    with op.batch_alter_table('trades') as batch:
        batch.add_column(sa.Column('broker_account_id', sa.Integer(), sa.ForeignKey('broker_accounts.id', ondelete='SET NULL'), nullable=True))
        batch.create_index('ix_trades_broker_account_id', ['broker_account_id'])


def downgrade() -> None:
    with op.batch_alter_table('trades') as batch:
        batch.drop_index('ix_trades_broker_account_id')
        batch.drop_column('broker_account_id')
    with op.batch_alter_table('tenants') as batch:
        batch.drop_column('default_routing_policy')
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.drop_column('last_route')
        batch.drop_column('route_across_brokers')
        batch.drop_column('routing_policy')

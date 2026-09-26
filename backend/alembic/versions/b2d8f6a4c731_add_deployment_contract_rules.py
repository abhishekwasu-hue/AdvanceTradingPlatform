"""add deployment contract rules (Phase F2): instrument_kind, option/expiry/strike rules,
premium stop, max lots; uniqueness now includes instrument_kind

Revision ID: b2d8f6a4c731
Revises: a1c9e7d3b520
Create Date: 2026-09-26 17:10:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b2d8f6a4c731'
down_revision: Union[str, Sequence[str], None] = 'a1c9e7d3b520'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.add_column(sa.Column('instrument_kind', sa.String(length=12), nullable=False, server_default='UNDERLYING'))
        batch.add_column(sa.Column('option_position', sa.String(length=6), nullable=True))
        batch.add_column(sa.Column('expiry_rule', sa.String(length=10), nullable=True))
        batch.add_column(sa.Column('strike_rule', sa.String(length=6), nullable=True))
        batch.add_column(sa.Column('strike_offset', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('premium_stop_pct', sa.Float(), nullable=True))
        batch.add_column(sa.Column('max_lots', sa.Integer(), nullable=True))
        batch.drop_constraint('uq_deployment_tenant_strategy_symbol_mode', type_='unique')
        batch.create_unique_constraint('uq_deployment_tenant_strategy_symbol_mode_kind',
                                       ['tenant_id', 'strategy_id', 'symbol', 'mode', 'instrument_kind'])


def downgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.drop_constraint('uq_deployment_tenant_strategy_symbol_mode_kind', type_='unique')
        batch.create_unique_constraint('uq_deployment_tenant_strategy_symbol_mode', ['tenant_id', 'strategy_id', 'symbol', 'mode'])
        batch.drop_column('max_lots')
        batch.drop_column('premium_stop_pct')
        batch.drop_column('strike_offset')
        batch.drop_column('strike_rule')
        batch.drop_column('expiry_rule')
        batch.drop_column('option_position')
        batch.drop_column('instrument_kind')

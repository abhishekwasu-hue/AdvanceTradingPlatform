"""trades: derived-contract columns (Phase F3) and nullable target1

Revision ID: c3e9a7b5d842
Revises: b2d8f6a4c731
Create Date: 2026-09-26 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3e9a7b5d842'
down_revision: Union[str, Sequence[str], None] = 'b2d8f6a4c731'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('trades') as batch:
        batch.alter_column('target1', existing_type=sa.Float(), nullable=True)
        batch.add_column(sa.Column('instrument_kind', sa.String(length=12), nullable=False, server_default='UNDERLYING'))
        batch.add_column(sa.Column('exchange', sa.String(length=20), nullable=True))
        batch.add_column(sa.Column('instrument_key', sa.String(length=100), nullable=True))
        batch.add_column(sa.Column('lot_size', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('expiry', sa.Date(), nullable=True))
        batch.add_column(sa.Column('option_position', sa.String(length=6), nullable=True))
        batch.add_column(sa.Column('premium_stop_pct', sa.Float(), nullable=True))
        batch.add_column(sa.Column('underlying_symbol', sa.String(length=50), nullable=True))
        batch.add_column(sa.Column('underlying_direction', sa.String(length=10), nullable=True))
        batch.add_column(sa.Column('underlying_stop_loss', sa.Float(), nullable=True))
        batch.add_column(sa.Column('underlying_target1', sa.Float(), nullable=True))
        batch.add_column(sa.Column('underlying_target2', sa.Float(), nullable=True))
        batch.add_column(sa.Column('expected_price', sa.Float(), nullable=True))
        batch.add_column(sa.Column('slippage', sa.Float(), nullable=True))
        batch.add_column(sa.Column('entry_latency_ms', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('trades') as batch:
        for column in ('entry_latency_ms', 'slippage', 'expected_price', 'underlying_target2', 'underlying_target1', 'underlying_stop_loss', 'underlying_direction',
                       'underlying_symbol', 'premium_stop_pct', 'option_position', 'expiry', 'lot_size',
                       'instrument_key', 'exchange', 'instrument_kind'):
            batch.drop_column(column)
        batch.alter_column('target1', existing_type=sa.Float(), nullable=False)

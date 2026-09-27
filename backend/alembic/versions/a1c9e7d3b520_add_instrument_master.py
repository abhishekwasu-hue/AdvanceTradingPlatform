"""add instrument master (Phase F1)

Revision ID: a1c9e7d3b520
Revises: f8c4d6e2b510
Create Date: 2026-09-26 16:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1c9e7d3b520'
down_revision: Union[str, Sequence[str], None] = 'f8c4d6e2b510'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'instruments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('broker', sa.String(length=50), nullable=False),
        sa.Column('exchange', sa.String(length=20), nullable=False),
        sa.Column('segment', sa.String(length=20), nullable=True),
        sa.Column('instrument_key', sa.String(length=100), nullable=False),
        sa.Column('tradingsymbol', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=True),
        sa.Column('underlying', sa.String(length=50), nullable=True),
        sa.Column('instrument_type', sa.String(length=10), nullable=False),
        sa.Column('expiry', sa.Date(), nullable=True),
        sa.Column('strike', sa.Float(), nullable=True),
        sa.Column('lot_size', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('tick_size', sa.Float(), nullable=False, server_default='0.05'),
        sa.Column('weekly', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('broker', 'exchange', 'instrument_key', name='uq_instrument_broker_key'),
    )
    op.create_index('ix_instruments_lookup', 'instruments', ['broker', 'underlying', 'instrument_type', 'expiry', 'strike'], unique=False)
    op.create_index('ix_instruments_symbol', 'instruments', ['broker', 'exchange', 'tradingsymbol'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_instruments_symbol', table_name='instruments')
    op.drop_index('ix_instruments_lookup', table_name='instruments')
    op.drop_table('instruments')

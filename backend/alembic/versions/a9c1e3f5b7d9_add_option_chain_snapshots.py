"""Phase W: recorded option-chain quotes for historical option backtests

Revision ID: a9c1e3f5b7d9
Revises: f8b1d3e5a7c9
Create Date: 2026-09-29 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a9c1e3f5b7d9'
down_revision: Union[str, Sequence[str], None] = 'f8b1d3e5a7c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'option_chain_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('underlying', sa.String(length=30), nullable=False),
        sa.Column('expiry', sa.Date(), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('strike', sa.Float(), nullable=False),
        sa.Column('right', sa.String(length=2), nullable=False),
        sa.Column('ltp', sa.Float(), nullable=False),
        sa.Column('iv', sa.Float(), nullable=True),
        sa.Column('oi', sa.Float(), nullable=True),
        sa.Column('underlying_ltp', sa.Float(), nullable=True),
        sa.Column('source', sa.String(length=30), nullable=False, server_default='worker'),
    )
    op.create_index('ix_option_chain_snapshots_captured_at', 'option_chain_snapshots', ['captured_at'])
    op.create_index('ix_option_chain_snapshots_lookup', 'option_chain_snapshots', ['underlying', 'expiry', 'captured_at'])


def downgrade() -> None:
    op.drop_index('ix_option_chain_snapshots_lookup', table_name='option_chain_snapshots')
    op.drop_index('ix_option_chain_snapshots_captured_at', table_name='option_chain_snapshots')
    op.drop_table('option_chain_snapshots')

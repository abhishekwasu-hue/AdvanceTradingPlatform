"""Screener addendum U1-a: NSE universe - securities (by ISIN) and symbol history (as-of ranges).

Revision ID: c7a1d2e3f4b5
Revises: b1c2d3e4f5a6
Create Date: 2026-10-11 01:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c7a1d2e3f4b5'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _provenance():
    return [sa.Column('source', sa.String(40), nullable=False),
            sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('checksum', sa.String(64), nullable=False)]


def upgrade() -> None:
    op.create_table(
        'securities',
        sa.Column('isin', sa.String(12), primary_key=True),
        sa.Column('symbol', sa.String(40), nullable=False), sa.Column('name', sa.String(200), nullable=False),
        sa.Column('series', sa.String(4), nullable=False),
        sa.Column('listing_date', sa.Date(), nullable=True), sa.Column('delisting_date', sa.Date(), nullable=True),
        sa.Column('face_value', sa.Numeric(18, 2), nullable=True), sa.Column('market_lot', sa.Integer(), nullable=True),
        sa.Column('exchanges', sa.String(20), nullable=False, server_default='NSE'),
        sa.Column('status', sa.String(12), nullable=False, server_default='ACTIVE'),
        sa.Column('is_sme', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('is_etf', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('underlying', sa.String(100), nullable=True),
        sa.Column('last_seen_on', sa.Date(), nullable=True),
        *_provenance(),
    )
    op.create_index('ix_securities_symbol', 'securities', ['symbol'])
    op.create_table(
        'symbol_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('isin', sa.String(12), nullable=False), sa.Column('symbol', sa.String(40), nullable=False),
        sa.Column('valid_from', sa.Date(), nullable=True), sa.Column('valid_to', sa.Date(), nullable=True),
        *_provenance(),
        sa.UniqueConstraint('isin', 'symbol', 'valid_from', name='uq_symbol_history_range'),
    )
    op.create_index('ix_symbol_history_isin', 'symbol_history', ['isin'])
    op.create_index('ix_symbol_history_symbol', 'symbol_history', ['symbol', 'valid_from'])


def downgrade() -> None:
    op.drop_index('ix_symbol_history_symbol', table_name='symbol_history')
    op.drop_index('ix_symbol_history_isin', table_name='symbol_history')
    op.drop_table('symbol_history')
    op.drop_index('ix_securities_symbol', table_name='securities')
    op.drop_table('securities')

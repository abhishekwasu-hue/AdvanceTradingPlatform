"""Screener addendum U1-c: index EOD closes, F&O membership/lot ranges, the exchange's F&O ban-list history.

Revision ID: c7a1d2e3f4b7
Revises: c7a1d2e3f4b6
Create Date: 2026-10-11 03:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c7a1d2e3f4b7'
down_revision: Union[str, Sequence[str], None] = 'c7a1d2e3f4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PRICE = sa.Numeric(18, 4)


def _provenance():
    return [sa.Column('source', sa.String(40), nullable=False),
            sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('checksum', sa.String(64), nullable=False)]


def upgrade() -> None:
    op.create_table(
        'index_eod',
        sa.Column('index_code', sa.String(40), primary_key=True), sa.Column('trade_date', sa.Date(), primary_key=True),
        sa.Column('open', PRICE, nullable=True), sa.Column('high', PRICE, nullable=True), sa.Column('low', PRICE, nullable=True),
        sa.Column('close', PRICE, nullable=False),
        sa.Column('pe', sa.Float(), nullable=True), sa.Column('pb', sa.Float(), nullable=True), sa.Column('div_yield', sa.Float(), nullable=True),
        *_provenance(),
    )
    op.create_table(
        'fo_membership',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('underlying', sa.String(40), nullable=False), sa.Column('isin', sa.String(12), nullable=True),
        sa.Column('is_index', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('lot_size', sa.Integer(), nullable=False),
        sa.Column('valid_from', sa.Date(), nullable=False), sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('start_observed', sa.Boolean(), nullable=False, server_default=sa.false()),
        *_provenance(),
        sa.UniqueConstraint('underlying', 'valid_from', name='uq_fo_membership_range'),
    )
    op.create_index('ix_fo_membership_underlying', 'fo_membership', ['underlying'])
    op.create_index('ix_fo_membership_isin', 'fo_membership', ['isin'])
    op.create_table(
        'fo_ban_history',
        sa.Column('underlying', sa.String(40), primary_key=True), sa.Column('trade_date', sa.Date(), primary_key=True),
        *_provenance(),
    )


def downgrade() -> None:
    op.drop_table('fo_ban_history')
    op.drop_index('ix_fo_membership_isin', table_name='fo_membership')
    op.drop_index('ix_fo_membership_underlying', table_name='fo_membership')
    op.drop_table('fo_membership')
    op.drop_table('index_eod')

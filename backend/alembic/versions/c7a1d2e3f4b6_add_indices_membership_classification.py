"""Screener addendum U1-b: indices, index membership ranges, industry classification ranges.

Revision ID: c7a1d2e3f4b6
Revises: c7a1d2e3f4b5
Create Date: 2026-10-11 02:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c7a1d2e3f4b6'
down_revision: Union[str, Sequence[str], None] = 'c7a1d2e3f4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _provenance():
    return [sa.Column('source', sa.String(40), nullable=False),
            sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('checksum', sa.String(64), nullable=False)]


def upgrade() -> None:
    op.create_table(
        'indices',
        sa.Column('index_code', sa.String(40), primary_key=True), sa.Column('name', sa.String(100), nullable=False),
        sa.Column('family', sa.String(20), nullable=False), sa.Column('base_date', sa.Date(), nullable=True),
        sa.Column('method', sa.String(60), nullable=True), sa.Column('broker_symbols', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('has_derivatives', sa.Boolean(), nullable=False, server_default=sa.false()),
        *_provenance(),
    )
    op.create_table(
        'index_membership',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('index_code', sa.String(40), nullable=False), sa.Column('isin', sa.String(12), nullable=False),
        sa.Column('weight', sa.Float(), nullable=True),
        sa.Column('valid_from', sa.Date(), nullable=False), sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('start_observed', sa.Boolean(), nullable=False, server_default=sa.false()),
        *_provenance(),
        sa.UniqueConstraint('index_code', 'isin', 'valid_from', name='uq_index_membership_range'),
    )
    op.create_index('ix_index_membership_isin', 'index_membership', ['isin'])
    op.create_index('ix_index_membership_lookup', 'index_membership', ['index_code', 'valid_from', 'valid_to'])
    op.create_table(
        'classifications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('isin', sa.String(12), nullable=False), sa.Column('scheme', sa.String(12), nullable=False, server_default='NSE'),
        sa.Column('macro_sector', sa.String(100), nullable=True), sa.Column('sector', sa.String(100), nullable=True),
        sa.Column('industry', sa.String(100), nullable=True), sa.Column('basic_industry', sa.String(100), nullable=True),
        sa.Column('valid_from', sa.Date(), nullable=False), sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('start_observed', sa.Boolean(), nullable=False, server_default=sa.false()),
        *_provenance(),
        sa.UniqueConstraint('isin', 'scheme', 'valid_from', name='uq_classification_range'),
    )
    op.create_index('ix_classifications_isin', 'classifications', ['isin'])


def downgrade() -> None:
    op.drop_index('ix_classifications_isin', table_name='classifications')
    op.drop_table('classifications')
    op.drop_index('ix_index_membership_lookup', table_name='index_membership')
    op.drop_index('ix_index_membership_isin', table_name='index_membership')
    op.drop_table('index_membership')
    op.drop_table('indices')

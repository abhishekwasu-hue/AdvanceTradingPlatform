"""Phase AR: market memory - snapshots of watched symbols and market cues

Revision ID: d3f5b7c9e1a2
Revises: c2e4a6b8d0f1
Create Date: 2026-10-04 14:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd3f5b7c9e1a2'
down_revision: Union[str, Sequence[str], None] = 'c2e4a6b8d0f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'market_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('exchange', sa.String(length=10), nullable=False),
        sa.Column('timeframe', sa.String(length=10), nullable=False),
        sa.Column('source', sa.String(length=40), nullable=False),
        sa.Column('last_price', sa.Float(), nullable=True),
        sa.Column('change_pct', sa.Float(), nullable=True),
        sa.Column('bias', sa.String(length=10), nullable=True),
        sa.Column('regime', sa.String(length=20), nullable=True),
        sa.Column('higher_regime', sa.String(length=20), nullable=True),
        sa.Column('structure', sa.String(length=12), nullable=True),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f('ix_market_snapshots_tenant_id'), 'market_snapshots', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_market_snapshots_captured_at'), 'market_snapshots', ['captured_at'], unique=False)
    op.create_index('ix_market_snapshots_lookup', 'market_snapshots', ['tenant_id', 'symbol', 'captured_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_market_snapshots_lookup', table_name='market_snapshots')
    op.drop_index(op.f('ix_market_snapshots_captured_at'), table_name='market_snapshots')
    op.drop_index(op.f('ix_market_snapshots_tenant_id'), table_name='market_snapshots')
    op.drop_table('market_snapshots')

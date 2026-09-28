"""Phase V1: Risk Guardian - guardian settings on risk_settings, market_events table

Revision ID: d6f8b1c3e5a7
Revises: c5e7a9b2d4f6
Create Date: 2026-09-28 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd6f8b1c3e5a7'
down_revision: Union[str, Sequence[str], None] = 'c5e7a9b2d4f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('risk_settings') as batch:
        batch.add_column(sa.Column('max_portfolio_risk_pct', sa.Float(), nullable=False, server_default='6.0'))
        batch.add_column(sa.Column('stop_cooldown_minutes', sa.Integer(), nullable=False, server_default='30'))
        batch.add_column(sa.Column('dd_level_1_pct', sa.Float(), nullable=False, server_default='5.0'))
        batch.add_column(sa.Column('dd_level_2_pct', sa.Float(), nullable=False, server_default='10.0'))
        batch.add_column(sa.Column('event_size_cut_pct', sa.Float(), nullable=False, server_default='50.0'))
    op.create_table(
        'market_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('underlying', sa.String(length=50), nullable=True),
        sa.Column('event_date', sa.Date(), nullable=False),
        sa.Column('start_time', sa.String(length=5), nullable=True),
        sa.Column('end_time', sa.String(length=5), nullable=True),
        sa.Column('kind', sa.String(length=30), nullable=False, server_default='OTHER'),
        sa.Column('action', sa.String(length=10), nullable=False, server_default='SIZE_CUT'),
        sa.Column('size_cut_pct', sa.Float(), nullable=True),
        sa.Column('description', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_market_events_tenant_id', 'market_events', ['tenant_id'])
    op.create_index('ix_market_events_event_date', 'market_events', ['event_date'])


def downgrade() -> None:
    op.drop_index('ix_market_events_event_date', table_name='market_events')
    op.drop_index('ix_market_events_tenant_id', table_name='market_events')
    op.drop_table('market_events')
    with op.batch_alter_table('risk_settings') as batch:
        for col in ('event_size_cut_pct', 'dd_level_2_pct', 'dd_level_1_pct', 'stop_cooldown_minutes', 'max_portfolio_risk_pct'):
            batch.drop_column(col)

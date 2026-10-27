"""TM-2: the resting order book (limit / SL / SL-M entries and exits waiting for their price) for the one trigger engine

Revision ID: 7e2a4c6b8d10
Revises: 3c5e7a9b1d24 (TM94-3, the Phase W recorder's tenant column)
Create Date: 2026-10-24 12:00:00

- paper_resting_orders: a new table only, owned by tenant AND user, with its idempotency key (tenant, user, client_ref)
  unique, the trigger settings (source premium / spot, level, side, touch / close, timeframe), the status and the order
  trail. Nothing existing is altered; no backfill.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '7e2a4c6b8d10'
down_revision: Union[str, Sequence[str], None] = '3c5e7a9b1d24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("a new table only: the previous release never reads or writes paper_resting_orders, so it runs "
                        "unchanged on the upgraded schema; the downgrade drops the table and its indexes")

PRICE = sa.Numeric(18, 4, asdecimal=False)


def upgrade() -> None:
    op.create_table(
        'paper_resting_orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('client_ref', sa.String(length=80), nullable=False),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('purpose', sa.String(length=10), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('trade_id', sa.Integer(), nullable=True),
        sa.Column('symbol', sa.String(length=64), nullable=False),
        sa.Column('exchange', sa.String(length=16), nullable=False),
        sa.Column('side', sa.String(length=4), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('order_type', sa.String(length=8), nullable=False),
        sa.Column('price', PRICE, nullable=True),
        sa.Column('trigger_price', PRICE, nullable=True),
        sa.Column('trigger_source', sa.String(length=8), nullable=False),
        sa.Column('trigger_symbol', sa.String(length=64), nullable=False),
        sa.Column('trigger_exchange', sa.String(length=16), nullable=False),
        sa.Column('trigger_level', PRICE, nullable=False),
        sa.Column('trigger_side', sa.String(length=6), nullable=False),
        sa.Column('trigger_mode', sa.String(length=6), nullable=False),
        sa.Column('timeframe_minutes', sa.Integer(), nullable=True),
        sa.Column('payload_json', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('status_note', sa.Text(), nullable=True),
        sa.Column('trail_json', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_price', PRICE, nullable=True),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fired_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fill_price', PRICE, nullable=True),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'user_id', 'client_ref', name='uq_resting_order_client_ref'),
    )
    op.create_index('ix_resting_orders_tenant_status', 'paper_resting_orders', ['tenant_id', 'status'], unique=False)
    op.create_index(op.f('ix_paper_resting_orders_user_id'), 'paper_resting_orders', ['user_id'], unique=False)
    op.create_index(op.f('ix_paper_resting_orders_trade_id'), 'paper_resting_orders', ['trade_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_paper_resting_orders_trade_id'), table_name='paper_resting_orders')
    op.drop_index(op.f('ix_paper_resting_orders_user_id'), table_name='paper_resting_orders')
    op.drop_index('ix_resting_orders_tenant_status', table_name='paper_resting_orders')
    op.drop_table('paper_resting_orders')

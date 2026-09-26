"""add orders and order_events (formal order state machine)

Revision ID: 9a3c6e1b2d47
Revises: 6f1a2d9b7c31
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a3c6e1b2d47'
down_revision: Union[str, Sequence[str], None] = '6f1a2d9b7c31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """New tables only - no backfill needed since there is no pre-existing order history to
    migrate (paper-execute previously wrote straight to `trades` with no order-lifecycle
    record at all).
    """
    op.create_table(
        'orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=200), nullable=True),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('strategy_id', sa.String(length=100), nullable=False),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('broker_order_id', sa.String(length=100), nullable=True),
        sa.Column('trade_id', sa.Integer(), nullable=True),
        sa.Column('signal_json', sa.Text(), nullable=False),
        sa.Column('reasons_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'idempotency_key', name='uq_tenant_idempotency_key'),
    )
    op.create_index(op.f('ix_orders_tenant_id'), 'orders', ['tenant_id'])
    op.create_index(op.f('ix_orders_status'), 'orders', ['status'])

    op.create_table(
        'order_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('from_status', sa.String(length=20), nullable=True),
        sa.Column('to_status', sa.String(length=20), nullable=False),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_order_events_order_id'), 'order_events', ['order_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_order_events_order_id'), table_name='order_events')
    op.drop_table('order_events')

    op.drop_index(op.f('ix_orders_status'), table_name='orders')
    op.drop_index(op.f('ix_orders_tenant_id'), table_name='orders')
    op.drop_table('orders')

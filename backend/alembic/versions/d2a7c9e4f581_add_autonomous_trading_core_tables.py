"""add autonomous trading core tables (Phase A1)

strategy_deployments, worker_heartbeats and market_holidays (seeded with the NSE capital-market
trading-holiday list for 2026), plus the broker token-lifecycle columns on broker_credentials
and the broker/SL order-id + deployment link columns on trades.

Revision ID: d2a7c9e4f581
Revises: c81f4e2a9d36
Create Date: 2026-09-26 10:26:17.153919

"""
from datetime import date
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2a7c9e4f581'
down_revision: Union[str, Sequence[str], None] = 'c81f4e2a9d36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# NSE Capital Market segment trading holidays for calendar year 2026, per NSE circular
# CMTR71775 ("Trading Holidays for the Calendar Year 2026"). Holidays that already fall on a
# Saturday/Sunday (Maha Shivaratri 15-Feb, Independence Day 15-Aug) are not listed since the
# market calendar (app/market_data/calendar.py) closes every weekend regardless. 08-Nov-2026
# (Sunday) is a trading holiday with a special Muhurat session whose timings NSE notifies
# separately; it is recorded so the row exists for operators, the weekend rule closes it anyway.
NSE_HOLIDAYS_2026 = [
    (date(2026, 1, 26), "Republic Day"),
    (date(2026, 3, 3), "Holi"),
    (date(2026, 3, 26), "Shri Ram Navami"),
    (date(2026, 3, 31), "Shri Mahavir Jayanti"),
    (date(2026, 4, 3), "Good Friday"),
    (date(2026, 4, 14), "Dr. Baba Saheb Ambedkar Jayanti"),
    (date(2026, 5, 1), "Maharashtra Day"),
    (date(2026, 5, 28), "Bakri Id"),
    (date(2026, 6, 26), "Muharram"),
    (date(2026, 9, 14), "Ganesh Chaturthi"),
    (date(2026, 10, 2), "Mahatma Gandhi Jayanti"),
    (date(2026, 10, 20), "Dussehra"),
    (date(2026, 11, 8), "Diwali Laxmi Pujan (Sunday - Muhurat trading session only)"),
    (date(2026, 11, 10), "Diwali Balipratipada"),
    (date(2026, 11, 24), "Gurunanak Jayanti"),
    (date(2026, 12, 25), "Christmas"),
]


def upgrade() -> None:
    op.create_table(
        'market_holidays',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('exchange', sa.String(length=20), nullable=False),
        sa.Column('holiday_date', sa.Date(), nullable=False),
        sa.Column('description', sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('exchange', 'holiday_date', name='uq_market_holiday'),
    )
    op.create_index(op.f('ix_market_holidays_holiday_date'), 'market_holidays', ['holiday_date'], unique=False)

    op.create_table(
        'worker_heartbeats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('worker_name', sa.String(length=100), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('cycle_count', sa.Integer(), nullable=False),
        sa.Column('last_cycle_ms', sa.Integer(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('worker_name'),
    )

    op.create_table(
        'strategy_deployments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('strategy_id', sa.String(length=100), nullable=False),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('exchange', sa.String(length=20), nullable=False),
        sa.Column('timeframe', sa.String(length=10), nullable=False),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('broker_name', sa.String(length=50), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('pause_reason', sa.Text(), nullable=True),
        sa.Column('last_evaluated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_signal_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('consecutive_failures', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tenant_id', 'strategy_id', 'symbol', 'mode', name='uq_deployment_tenant_strategy_symbol_mode'
        ),
    )
    op.create_index(op.f('ix_strategy_deployments_status'), 'strategy_deployments', ['status'], unique=False)
    op.create_index(op.f('ix_strategy_deployments_tenant_id'), 'strategy_deployments', ['tenant_id'], unique=False)

    # Existing credential rows predate token tracking: UNKNOWN until the next successful
    # authenticate/OAuth callback proves the token and records its expiry.
    op.add_column(
        'broker_credentials',
        sa.Column('token_status', sa.String(length=20), nullable=False, server_default='UNKNOWN'),
    )
    op.add_column('broker_credentials', sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('broker_credentials', sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True))

    op.add_column('trades', sa.Column('broker_order_id', sa.String(length=100), nullable=True))
    op.add_column('trades', sa.Column('sl_order_id', sa.String(length=100), nullable=True))
    op.add_column('trades', sa.Column('deployment_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_trades_deployment_id', 'trades', 'strategy_deployments', ['deployment_id'], ['id'], ondelete='SET NULL'
    )

    market_holidays = sa.table(
        'market_holidays',
        sa.column('exchange', sa.String()), sa.column('holiday_date', sa.Date()), sa.column('description', sa.String()),
    )
    op.bulk_insert(
        market_holidays,
        [{'exchange': 'NSE', 'holiday_date': d, 'description': name} for d, name in NSE_HOLIDAYS_2026],
    )


def downgrade() -> None:
    op.drop_constraint('fk_trades_deployment_id', 'trades', type_='foreignkey')
    op.drop_column('trades', 'deployment_id')
    op.drop_column('trades', 'sl_order_id')
    op.drop_column('trades', 'broker_order_id')
    op.drop_column('broker_credentials', 'last_verified_at')
    op.drop_column('broker_credentials', 'token_expires_at')
    op.drop_column('broker_credentials', 'token_status')
    op.drop_index(op.f('ix_strategy_deployments_tenant_id'), table_name='strategy_deployments')
    op.drop_index(op.f('ix_strategy_deployments_status'), table_name='strategy_deployments')
    op.drop_table('strategy_deployments')
    op.drop_table('worker_heartbeats')
    op.drop_index(op.f('ix_market_holidays_holiday_date'), table_name='market_holidays')
    op.drop_table('market_holidays')

"""Part B1: market data lake schema (ADR-0013) - candles, ticks, option chains, position limits, instrument master
versions, corporate actions, data quality events. On TimescaleDB the four time-series tables become hypertables.

The Timescale step runs only when the extension is installable on this server (`pg_available_extensions`), so the
Hostinger PAPER go-live on plain Postgres is unaffected (OPEN_QUESTIONS B-1); the tables work either way. Downgrade
drops the tables (the extension, if created here, is left installed - harmless, and other databases may use it).

Revision ID: b1c2d3e4f5a6
Revises: e6a1b2c3d4f5
Create Date: 2026-10-10 23:20:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'e6a1b2c3d4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PRICE = sa.Numeric(18, 4)
HYPERTABLES = {"md_candles": "ts", "md_ticks": "ts", "md_option_chain_snapshots": "ts", "md_position_limits": "trade_date"}


def _ingested():
    return sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=False)


def _timescale_available(bind) -> bool:
    if bind.dialect.name != "postgresql":
        return False
    return bool(bind.execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")).scalar())


def upgrade() -> None:
    op.create_table(
        'md_candles',
        sa.Column('instrument_key', sa.String(64), nullable=False), sa.Column('timeframe', sa.String(8), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False), sa.Column('source', sa.String(30), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('open', PRICE, nullable=False), sa.Column('high', PRICE, nullable=False), sa.Column('low', PRICE, nullable=False),
        sa.Column('close', PRICE, nullable=False), sa.Column('volume', sa.BigInteger(), nullable=False),
        sa.Column('oi', sa.BigInteger(), nullable=True), _ingested(),
        sa.PrimaryKeyConstraint('instrument_key', 'timeframe', 'ts', 'source', 'version'),
    )
    op.create_table(
        'md_ticks',
        sa.Column('instrument_key', sa.String(64), nullable=False), sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('seq', sa.BigInteger(), nullable=False), sa.Column('source', sa.String(30), nullable=False),
        sa.Column('ltp', PRICE, nullable=False), sa.Column('volume', sa.BigInteger(), nullable=True),
        sa.Column('oi', sa.BigInteger(), nullable=True), _ingested(),
        sa.PrimaryKeyConstraint('instrument_key', 'ts', 'seq', 'source'),
    )
    op.create_table(
        'md_option_chain_snapshots',
        sa.Column('underlying', sa.String(40), nullable=False), sa.Column('expiry', sa.Date(), nullable=False),
        sa.Column('strike', PRICE, nullable=False), sa.Column('option_type', sa.String(2), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False), sa.Column('source', sa.String(30), nullable=False),
        sa.Column('ltp', PRICE, nullable=True), sa.Column('oi', sa.BigInteger(), nullable=True),
        sa.Column('volume', sa.BigInteger(), nullable=True), sa.Column('iv', sa.Float(), nullable=True), _ingested(),
        sa.PrimaryKeyConstraint('underlying', 'expiry', 'strike', 'option_type', 'ts', 'source'),
    )
    op.create_table(
        'md_position_limits',
        sa.Column('underlying', sa.String(40), nullable=False), sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('source', sa.String(30), nullable=False), sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('mwpl', sa.BigInteger(), nullable=False), sa.Column('open_interest', sa.BigInteger(), nullable=False), _ingested(),
        sa.PrimaryKeyConstraint('underlying', 'trade_date', 'source', 'version'),
    )
    op.create_table(
        'instrument_master_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('instrument_key', sa.String(64), nullable=False), sa.Column('exchange', sa.String(20), nullable=False),
        sa.Column('tradingsymbol', sa.String(64), nullable=False), sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('lot_size', sa.Integer(), nullable=False), sa.Column('tick_size', PRICE, nullable=False),
        sa.Column('expiry', sa.Date(), nullable=True), sa.Column('strike', PRICE, nullable=True),
        sa.Column('valid_from', sa.Date(), nullable=False), sa.Column('source', sa.String(30), nullable=False), _ingested(),
        sa.UniqueConstraint('instrument_key', 'valid_from', 'source', name='uq_instrument_master_version'),
    )
    op.create_index('ix_instrument_master_versions_instrument_key', 'instrument_master_versions', ['instrument_key'])
    op.create_table(
        'md_corporate_actions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('symbol', sa.String(40), nullable=False), sa.Column('exchange', sa.String(20), nullable=False),
        sa.Column('ex_date', sa.Date(), nullable=False), sa.Column('action', sa.String(12), nullable=False),
        sa.Column('ratio_new', sa.Float(), nullable=True), sa.Column('ratio_old', sa.Float(), nullable=True),
        sa.Column('amount', sa.Numeric(18, 2), nullable=True), sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(30), nullable=False), _ingested(),
        sa.UniqueConstraint('symbol', 'exchange', 'ex_date', 'action', 'version', name='uq_md_corporate_action_version'),
    )
    op.create_index('ix_md_corporate_actions_symbol', 'md_corporate_actions', ['symbol'])
    op.create_table(
        'data_quality_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(12), nullable=False), sa.Column('instrument_key', sa.String(64), nullable=False),
        sa.Column('timeframe', sa.String(8), nullable=True), sa.Column('ts', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source', sa.String(30), nullable=False), sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_data_quality_events_kind', 'data_quality_events', ['kind'])
    op.create_index('ix_data_quality_events_instrument_key', 'data_quality_events', ['instrument_key'])

    bind = op.get_bind()
    if _timescale_available(bind):
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        for table, column in HYPERTABLES.items():
            op.execute(f"SELECT create_hypertable('{table}', '{column}', if_not_exists => TRUE, migrate_data => TRUE)")


def downgrade() -> None:
    op.drop_index('ix_data_quality_events_instrument_key', table_name='data_quality_events')
    op.drop_index('ix_data_quality_events_kind', table_name='data_quality_events')
    op.drop_table('data_quality_events')
    op.drop_index('ix_md_corporate_actions_symbol', table_name='md_corporate_actions')
    op.drop_table('md_corporate_actions')
    op.drop_index('ix_instrument_master_versions_instrument_key', table_name='instrument_master_versions')
    op.drop_table('instrument_master_versions')
    for table in ('md_position_limits', 'md_option_chain_snapshots', 'md_ticks', 'md_candles'):
        op.drop_table(table)

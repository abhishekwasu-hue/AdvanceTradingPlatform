"""HB1: daily NSE bhavcopy backfill - per-file state and the observed ISIN <-> symbol ranges.

- `history_backfill_files`: one row per (segment, trade date) - cm / fo / index - the backfill's resume point and the
  admin progress source (status, checksum, rows, quality events, last error).
- `md_isin_symbols`: which symbol an ISIN traded under, as observed in the exchange's daily cash files - one row per
  contiguous run (first / last trade date) - survivorship-free, and the join that stitches a company's history across
  symbol and ISIN changes.

Bars themselves go to the existing `md_candles` (timeframe `day`, sources `nse_bhav:cm|fo|index`); no column changes.

Revision ID: c5e7a9b1d3f4
Revises: a8c0e2f4b6d8
Create Date: 2026-10-11 21:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c5e7a9b1d3f4'
down_revision: Union[str, Sequence[str], None] = 'a8c0e2f4b6d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'history_backfill_files',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('segment', sa.String(8), nullable=False), sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False), sa.Column('origin', sa.String(16), nullable=True),
        sa.Column('file_name', sa.String(200), nullable=True), sa.Column('url', sa.String(400), nullable=True),
        sa.Column('file_format', sa.String(16), nullable=True), sa.Column('volume_unit', sa.String(16), nullable=True),
        sa.Column('sha256', sa.String(64), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True), sa.Column('http_status', sa.Integer(), nullable=True),
        sa.Column('rows_read', sa.Integer(), nullable=True), sa.Column('rows_loaded', sa.Integer(), nullable=True),
        sa.Column('inserted', sa.Integer(), nullable=False), sa.Column('corrected', sa.Integer(), nullable=False),
        sa.Column('skipped', sa.Integer(), nullable=False), sa.Column('quality_events', sa.Integer(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True), sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True), sa.Column('started_at', TZ, nullable=True),
        sa.Column('done_at', TZ, nullable=True), sa.Column('updated_at', TZ, nullable=False),
        sa.UniqueConstraint('segment', 'trade_date', name='uq_history_backfill_file'),
    )
    op.create_index('ix_history_backfill_files_trade_date', 'history_backfill_files', ['trade_date'])
    op.create_index('ix_history_backfill_files_status', 'history_backfill_files', ['status'])
    op.create_table(
        'md_isin_symbols',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('exchange', sa.String(20), nullable=False), sa.Column('isin', sa.String(12), nullable=False),
        sa.Column('symbol', sa.String(40), nullable=False), sa.Column('series', sa.String(4), nullable=False),
        sa.Column('first_date', sa.Date(), nullable=False), sa.Column('last_date', sa.Date(), nullable=False),
        sa.Column('updated_at', TZ, nullable=False),
        sa.UniqueConstraint('exchange', 'isin', 'symbol', 'first_date', name='uq_md_isin_symbol_run'),
    )
    op.create_index('ix_md_isin_symbols_isin', 'md_isin_symbols', ['isin'])
    op.create_index('ix_md_isin_symbols_symbol', 'md_isin_symbols', ['exchange', 'symbol'])


def downgrade() -> None:
    op.drop_index('ix_md_isin_symbols_symbol', table_name='md_isin_symbols')
    op.drop_index('ix_md_isin_symbols_isin', table_name='md_isin_symbols')
    op.drop_table('md_isin_symbols')
    op.drop_index('ix_history_backfill_files_status', table_name='history_backfill_files')
    op.drop_index('ix_history_backfill_files_trade_date', table_name='history_backfill_files')
    op.drop_table('history_backfill_files')

"""HB0: R2 history import - the tenant-private candle store, tenant scope on data-quality events, the import state.

- `md_tenant_candles`: same shape as `md_candles` plus `tenant_id` in the primary key - the owner's licensed 1-minute
  history lives here and nowhere else (no shared read path queries this table). A hypertable on TimescaleDB, like
  md_candles (only when the extension is available).
- `data_quality_events.tenant_id` (nullable): NULL = about the shared lake; set = about that tenant's private store.
- `history_import_files`: one row per object of the R2 store - the importer's resume point and the HB4 progress source.

Revision ID: a8c0e2f4b6d8
Revises: e4c2a9b7d1f6
Create Date: 2026-10-11 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a8c0e2f4b6d8'
down_revision: Union[str, Sequence[str], None] = 'e4c2a9b7d1f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PRICE = sa.Numeric(18, 4)
TZ = sa.DateTime(timezone=True)


def _timescale_available(bind) -> bool:
    if bind.dialect.name != "postgresql":
        return False
    return bool(bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")).scalar())


def upgrade() -> None:
    op.create_table(
        'md_tenant_candles',
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('instrument_key', sa.String(64), nullable=False), sa.Column('timeframe', sa.String(8), nullable=False),
        sa.Column('ts', TZ, nullable=False), sa.Column('source', sa.String(30), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('open', PRICE, nullable=False), sa.Column('high', PRICE, nullable=False), sa.Column('low', PRICE, nullable=False),
        sa.Column('close', PRICE, nullable=False), sa.Column('volume', sa.BigInteger(), nullable=False),
        sa.Column('oi', sa.BigInteger(), nullable=True), sa.Column('ingested_at', TZ, nullable=False),
        sa.PrimaryKeyConstraint('tenant_id', 'instrument_key', 'timeframe', 'ts', 'source', 'version'),
    )
    op.add_column('data_quality_events', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_data_quality_events_tenant_id', 'data_quality_events', 'tenants', ['tenant_id'], ['id'],
                          ondelete='CASCADE')
    op.create_index('ix_data_quality_events_tenant_id', 'data_quality_events', ['tenant_id'])
    op.create_table(
        'history_import_files',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('object_key', sa.String(512), nullable=False, unique=True),
        sa.Column('manifest_key', sa.String(512), nullable=False),
        sa.Column('dataset', sa.String(8), nullable=False), sa.Column('exchange', sa.String(20), nullable=False),
        sa.Column('symbol', sa.String(40), nullable=False), sa.Column('period', sa.String(7), nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False), sa.Column('etag', sa.String(128), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('rows_expected', sa.Integer(), nullable=False), sa.Column('rows_read', sa.Integer(), nullable=True),
        sa.Column('rows_in_lake', sa.Integer(), nullable=True),
        sa.Column('inserted', sa.Integer(), nullable=False), sa.Column('corrected', sa.Integer(), nullable=False),
        sa.Column('skipped', sa.Integer(), nullable=False), sa.Column('quality_events', sa.Integer(), nullable=False),
        sa.Column('quality_note', sa.Text(), nullable=True), sa.Column('isin', sa.String(12), nullable=True),
        sa.Column('first_ts', TZ, nullable=True), sa.Column('last_ts', TZ, nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False), sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', TZ, nullable=True), sa.Column('imported_at', TZ, nullable=True),
        sa.Column('updated_at', TZ, nullable=False),
    )
    op.create_index('ix_history_import_files_status', 'history_import_files', ['status'])
    op.create_index('ix_history_import_files_dataset_symbol', 'history_import_files', ['dataset', 'exchange', 'symbol'])

    bind = op.get_bind()
    if _timescale_available(bind):      # md_candles became a hypertable in b1c2d3e4f5a6 when the extension was there
        op.execute("SELECT create_hypertable('md_tenant_candles', 'ts', if_not_exists => TRUE, migrate_data => TRUE)")


def downgrade() -> None:
    op.drop_index('ix_history_import_files_dataset_symbol', table_name='history_import_files')
    op.drop_index('ix_history_import_files_status', table_name='history_import_files')
    op.drop_table('history_import_files')
    # Events about a tenant's PRIVATE store go with that store: without the column they would read as shared-lake events.
    op.execute("DELETE FROM data_quality_events WHERE tenant_id IS NOT NULL")
    op.drop_index('ix_data_quality_events_tenant_id', table_name='data_quality_events')
    op.drop_constraint('fk_data_quality_events_tenant_id', 'data_quality_events', type_='foreignkey')
    op.drop_column('data_quality_events', 'tenant_id')
    op.drop_table('md_tenant_candles')

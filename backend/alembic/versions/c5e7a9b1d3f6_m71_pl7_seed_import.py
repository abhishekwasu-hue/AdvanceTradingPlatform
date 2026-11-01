"""M71 PL-7 (owner order M71, addendum PL-7): seed the market-data store from the owner's private data.

- `md_tenant_candles.volume` allows NULL: a seeded file without a volume column (or F&O volume withheld while its unit is
  unsettled) keeps its volume MISSING instead of a written 0. Every existing row keeps its value; no row is rewritten.
- `data_use_windows` (new): the private data's data-use windows (sealed / contaminated / in-sample / validation) per
  tenant, loaded by the import from the private policy - no dates in public code.

Per-file bookkeeping reuses HB0's `history_import_files` (dataset "seed"); gaps reuse PL-1c's `lake_backfill_windows`.

Downgrade drops `data_use_windows` and restores NOT NULL on the volume column, writing 0 only where it is NULL (the
previous release's readers need a number), so the previous release reads every row.

Revision ID: c5e7a9b1d3f6
Revises: c4e8a2f6b0d3
Create Date: 2026-11-01 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c5e7a9b1d3f6'
down_revision: str | Sequence[str] | None = 'c4e8a2f6b0d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = False    # Z11: md_tenant_candles.volume may hold NULL once the seed import has run - the previous release's private-store readers expect a number; a code-only rollback past it is refused, use --restore-db (or this downgrade)

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table('md_tenant_candles') as batch:
        batch.alter_column('volume', existing_type=sa.BigInteger(), existing_nullable=False, nullable=True)
    op.create_table(
        'data_use_windows',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('scope', sa.String(40), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('label', sa.String(60), nullable=True),
        sa.Column('use_note', sa.Text(), nullable=True),
        sa.Column('policy_source', sa.String(200), nullable=True),
        sa.Column('policy_sha256', sa.String(64), nullable=True),
        sa.Column('created_at', TZ, nullable=False),
        sa.Column('updated_at', TZ, nullable=False),
        sa.UniqueConstraint('tenant_id', 'kind', 'scope', 'start_date', name='uq_data_use_window'),
    )
    op.create_index('ix_data_use_windows_tenant_id', 'data_use_windows', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_data_use_windows_tenant_id', table_name='data_use_windows')
    op.drop_table('data_use_windows')
    op.execute("UPDATE md_tenant_candles SET volume = 0 WHERE volume IS NULL")
    with op.batch_alter_table('md_tenant_candles') as batch:
        batch.alter_column('volume', existing_type=sa.BigInteger(), existing_nullable=True, nullable=False)

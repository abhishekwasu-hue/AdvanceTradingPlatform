"""P2-8 Journal v2 J1 (docs/plans/journal-v2-PLAN.md section 2): broker-synced trades and stored chart pictures.

Two new tables and one nullable column, nothing else changes: `journal_broker_fills` (each fill read from the trader's
own broker trade book; unique per user + broker + fill id, so a re-run journals nothing twice), `journal_snapshots`
(the stored entry / exit chart picture of an ATP trade; the file is in the private store) and
`journal_manual_trades.origin` ("BROKER" for a synced entry, null otherwise).

After 15:30 IST (owner, DECISIONS 25 Oct).

Revision ID: e3a5c7e9b1d4
Revises: d2f4b6c8e0a1
Create Date: 2026-11-21 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'e3a5c7e9b1d4'
down_revision: str | Sequence[str] | None = 'd2f4b6c8e0a1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("new tables and one nullable column only: the previous release never reads or writes them, so a "
                        "code-only rollback leaves them unused and intact")


def upgrade() -> None:
    op.create_table('journal_broker_fills',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('broker', sa.String(length=30), nullable=False),
    sa.Column('fill_id', sa.String(length=100), nullable=False),
    sa.Column('order_id', sa.String(length=100), nullable=True),
    sa.Column('symbol', sa.String(length=50), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('price', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('filled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('trade_id', sa.Integer(), nullable=True),
    sa.Column('manual_trade_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['manual_trade_id'], ['journal_manual_trades.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'broker', 'fill_id', name='uq_journal_broker_fill'))
    op.create_index('ix_journal_broker_fills_tenant_id', 'journal_broker_fills', ['tenant_id'])
    op.create_index('ix_journal_broker_fills_user_id', 'journal_broker_fills', ['user_id'])
    op.create_table('journal_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('trade_id', sa.Integer(), nullable=False),
    sa.Column('at', sa.String(length=5), nullable=False),
    sa.Column('image_path', sa.String(length=80), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('trade_id', 'at', name='uq_journal_snapshot_trade_at'))
    op.create_index('ix_journal_snapshots_tenant_id', 'journal_snapshots', ['tenant_id'])
    op.create_index('ix_journal_snapshots_user_id', 'journal_snapshots', ['user_id'])
    op.add_column('journal_manual_trades', sa.Column('origin', sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column('journal_manual_trades', 'origin')
    op.drop_index('ix_journal_snapshots_user_id', table_name='journal_snapshots')
    op.drop_index('ix_journal_snapshots_tenant_id', table_name='journal_snapshots')
    op.drop_table('journal_snapshots')
    op.drop_index('ix_journal_broker_fills_user_id', table_name='journal_broker_fills')
    op.drop_index('ix_journal_broker_fills_tenant_id', table_name='journal_broker_fills')
    op.drop_table('journal_broker_fills')

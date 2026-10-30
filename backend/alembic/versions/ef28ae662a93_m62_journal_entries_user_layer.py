"""M62 JN-2: the trader's own journal layer - checklist and own tags on a journal entry, manual (outside-ATP) trades,
image attachments.

* `journal_entries.checklist_json`, `journal_entries.user_tags_json`: two new NULLABLE columns (no default needed).
* `journal_manual_trades`: a new table (journal data only; never read by any order, position or risk path).
* `journal_attachments`: a new table (the image file lives in the private store; the row holds its server-made path).

No data changes.

Rollback caveats:
* Code-only rollback onto this schema: purely additive. The previous release never reads the two new tables, and the
  two new columns are nullable, so its inserts into `journal_entries` (which do not name them) still succeed.
* `downgrade()` drops the two tables and the two columns: manual trades, checklists, own tags and the attachment rows
  are lost (the image files stay in the private store, unreferenced).

Revision ID: ef28ae662a93
Revises: f4b6d8a0c2e3
Create Date: 2026-10-30 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'ef28ae662a93'
down_revision: Union[str, Sequence[str], None] = 'f4b6d8a0c2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: new tables and nullable columns only - the previous release's code keeps working


def upgrade() -> None:
    op.add_column('journal_entries', sa.Column('checklist_json', sa.Text(), nullable=True))
    op.add_column('journal_entries', sa.Column('user_tags_json', sa.Text(), nullable=True))

    op.create_table(
        'journal_manual_trades',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('exchange', sa.String(length=20), nullable=True),
        sa.Column('instrument_kind', sa.String(length=12), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('entry_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('entry_price', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=False),
        sa.Column('stop_loss', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=True),
        sa.Column('target', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=True),
        sa.Column('exit_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('exit_price', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=True),
        sa.Column('exit_reason', sa.String(length=100), nullable=True),
        sa.Column('charges', sa.Numeric(precision=18, scale=2, asdecimal=False), nullable=False),
        sa.Column('setup', sa.String(length=100), nullable=True),
        sa.Column('timeframe', sa.String(length=10), nullable=True),
        sa.Column('area', sa.String(length=200), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('checklist_json', sa.Text(), nullable=True),
        sa.Column('user_tags_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_journal_manual_trades_tenant_id'), 'journal_manual_trades', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_journal_manual_trades_user_id'), 'journal_manual_trades', ['user_id'], unique=False)

    op.create_table(
        'journal_attachments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('trade_id', sa.Integer(), nullable=True),
        sa.Column('manual_trade_id', sa.Integer(), nullable=True),
        sa.Column('image_path', sa.String(length=80), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['manual_trade_id'], ['journal_manual_trades.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_journal_attachments_tenant_id'), 'journal_attachments', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_journal_attachments_user_id'), 'journal_attachments', ['user_id'], unique=False)
    op.create_index(op.f('ix_journal_attachments_trade_id'), 'journal_attachments', ['trade_id'], unique=False)
    op.create_index(op.f('ix_journal_attachments_manual_trade_id'), 'journal_attachments', ['manual_trade_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_journal_attachments_manual_trade_id'), table_name='journal_attachments')
    op.drop_index(op.f('ix_journal_attachments_trade_id'), table_name='journal_attachments')
    op.drop_index(op.f('ix_journal_attachments_user_id'), table_name='journal_attachments')
    op.drop_index(op.f('ix_journal_attachments_tenant_id'), table_name='journal_attachments')
    op.drop_table('journal_attachments')
    op.drop_index(op.f('ix_journal_manual_trades_user_id'), table_name='journal_manual_trades')
    op.drop_index(op.f('ix_journal_manual_trades_tenant_id'), table_name='journal_manual_trades')
    op.drop_table('journal_manual_trades')
    op.drop_column('journal_entries', 'user_tags_json')
    op.drop_column('journal_entries', 'checklist_json')

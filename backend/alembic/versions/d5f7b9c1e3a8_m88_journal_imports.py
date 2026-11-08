"""M88: journal_imports - the files of outside-ATP trades a trader imported - and journal_manual_trades.import_id.

Additive: one new table and one nullable column, both read only by the journal import. The previous release never
selects either, so a rollback leaves them in place unused.

Revision ID: d5f7b9c1e3a8
Revises: b3d5f7a9c1e4
Create Date: 2026-11-08 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'd5f7b9c1e3a8'
down_revision: Union[str, Sequence[str], None] = 'b3d5f7a9c1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - a new table and a nullable column the previous release never reads


def upgrade() -> None:
    op.create_table(
        'journal_imports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('file_hash', sa.String(64), nullable=False),
        sa.Column('file_name', sa.String(200), nullable=False),
        sa.Column('layout', sa.String(30), nullable=False),
        sa.Column('rows', sa.Integer(), nullable=False),
        sa.Column('imported', sa.Integer(), nullable=False),
        sa.Column('skipped', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'file_hash', name='uq_journal_import_user_hash'),
    )
    op.create_index('ix_journal_imports_tenant_id', 'journal_imports', ['tenant_id'])
    op.create_index('ix_journal_imports_user_id', 'journal_imports', ['user_id'])
    op.add_column('journal_manual_trades', sa.Column('import_id', sa.Integer(), nullable=True))
    op.create_index('ix_journal_manual_trades_import_id', 'journal_manual_trades', ['import_id'])


def downgrade() -> None:
    op.drop_index('ix_journal_manual_trades_import_id', table_name='journal_manual_trades')
    with op.batch_alter_table('journal_manual_trades') as batch:
        batch.drop_column('import_id')
    op.drop_index('ix_journal_imports_user_id', table_name='journal_imports')
    op.drop_index('ix_journal_imports_tenant_id', table_name='journal_imports')
    op.drop_table('journal_imports')

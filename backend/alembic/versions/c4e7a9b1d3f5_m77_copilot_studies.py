"""M77 SX-2 (owner order M77 PD-1, M77Q-3): the `copilot_studies` table - one saved Copilot study per row (the server's
snapshot as JSON with its SHA-256; PDF / PNG / CSV are rebuilt from it on download, never stored as files).

Revision ID: c4e7a9b1d3f5
Revises: c4e8a2f6b031
Create Date: 2026-11-01 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c4e7a9b1d3f5'
down_revision: str | Sequence[str] | None = 'c4e8a2f6b031'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("a new table only: no existing column, constraint or row changes; the previous release never reads "
                       "or writes copilot_studies, so a code-only rollback leaves it unused and intact")


def upgrade() -> None:
    op.create_table(
        'copilot_studies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=60), nullable=False),
        sa.Column('exchange', sa.String(length=20), nullable=False),
        sa.Column('instrument_kind', sa.String(length=20), nullable=True),
        sa.Column('timeframes', sa.String(length=40), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=True),
        sa.Column('sections_json', sa.Text(), nullable=False),
        sa.Column('snapshot_json', sa.Text(), nullable=False),
        sa.Column('snapshot_sha256', sa.String(length=64), nullable=False),
        sa.Column('reading_source', sa.String(length=10), nullable=False),
        sa.Column('app_version', sa.String(length=40), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('share_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_copilot_studies_tenant_id', 'copilot_studies', ['tenant_id'], unique=False)
    op.create_index('ix_copilot_studies_user_id', 'copilot_studies', ['user_id'], unique=False)
    op.create_index('ix_copilot_studies_user_symbol_created', 'copilot_studies', ['user_id', 'symbol', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_copilot_studies_user_symbol_created', table_name='copilot_studies')
    op.drop_index('ix_copilot_studies_user_id', table_name='copilot_studies')
    op.drop_index('ix_copilot_studies_tenant_id', table_name='copilot_studies')
    op.drop_table('copilot_studies')

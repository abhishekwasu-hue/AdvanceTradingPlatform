"""WL1 (handover item 4): read-only system watchlists (indices, index stocks, sectors, F&O stocks) and their refresh log.

Revision ID: a4c6e8f0b2d4
Revises: c4e6a8b0d2f4 (Analyze a chart - main's head when WL1 merged)
Create Date: 2026-10-22 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a4c6e8f0b2d4'
down_revision: Union[str, Sequence[str], None] = 'c4e6a8b0d2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - two new tables that no earlier release reads or writes, so the previous release runs unchanged
# on this schema (a code-only rollback is fine; the tables are simply ignored).
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.create_table(
        'system_watchlists',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('group', sa.String(length=16), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('items_json', sa.Text(), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('checksum', sa.String(length=64), nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('refreshed_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('key'),
    )
    op.create_index('ix_system_watchlists_group', 'system_watchlists', ['group'])
    op.create_table(
        'system_watchlist_refreshes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lists_total', sa.Integer(), nullable=False),
        sa.Column('lists_changed', sa.Integer(), nullable=False),
        sa.Column('changes_json', sa.Text(), nullable=False),
        sa.Column('unmapped_json', sa.Text(), nullable=False),
        sa.Column('unmapped_count', sa.Integer(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
    )
    op.create_index('ix_system_watchlist_refreshes_started_at', 'system_watchlist_refreshes', ['started_at'])


def downgrade() -> None:
    op.drop_index('ix_system_watchlist_refreshes_started_at', table_name='system_watchlist_refreshes')
    op.drop_table('system_watchlist_refreshes')
    op.drop_index('ix_system_watchlists_group', table_name='system_watchlists')
    op.drop_table('system_watchlists')

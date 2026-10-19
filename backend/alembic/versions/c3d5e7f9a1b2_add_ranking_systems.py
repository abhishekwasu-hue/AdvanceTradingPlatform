"""N3 S7a: ranking_systems - saved weighted factor trees that rank a universe 0-100 (flag screener_rankings, off).

Revision ID: c3d5e7f9a1b2
Revises: f3b5d7e9a1c2 (AMW-2 watchlists, #267 - main's head; re-chained from e2a4c6e8f0b3, earlier e2a4c6b8d0f1, c9e1f3a5b7d2)
Create Date: 2026-10-12 21:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3d5e7f9a1b2'
down_revision: Union[str, Sequence[str], None] = 'f3b5d7e9a1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - one new table that no earlier release reads or writes, so the previous release runs unchanged
# on this schema (a code-only rollback is fine; the table is simply ignored).
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.create_table(
        'ranking_systems',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('description', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('tree_json', sa.Text(), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'name', name='uq_ranking_systems_tenant_name'),
    )
    op.create_index('ix_ranking_systems_tenant_id', 'ranking_systems', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_ranking_systems_tenant_id', table_name='ranking_systems')
    op.drop_table('ranking_systems')

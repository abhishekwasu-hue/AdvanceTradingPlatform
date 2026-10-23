"""LAB-18 (AMW-7): immutable versions of a saved screen - screen_versions, and screens.versions_imported_at (the marker of
the one upload of a device's history)

Revision ID: c6e8a0b2d4f7
Revises: b5d7f9a1c3e6 (CP6-15 losses-in-a-row pause, #423 - part of the same migration block)
Create Date: 2026-10-23 16:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c6e8a0b2d4f7'
down_revision: Union[str, Sequence[str], None] = 'b5d7f9a1c3e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - a new table no earlier release reads or writes and a nullable column on screens that the
# previous release never reads or writes, so it runs unchanged on this schema (a code-only rollback is fine).
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.create_table(
        'screen_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('screen_id', sa.Integer(), sa.ForeignKey('screens.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('source_text', sa.Text(), nullable=False),
        sa.Column('ast_json', sa.Text(), nullable=False),
        sa.Column('ast_version', sa.String(length=20), nullable=False),
        sa.Column('base_tf', sa.String(length=4), nullable=False),
        sa.Column('params_json', sa.Text(), nullable=False),
        sa.Column('body_sha256', sa.String(length=64), nullable=False),
        sa.Column('change', sa.String(length=600), nullable=False),
        sa.Column('builder_json', sa.Text(), nullable=True),
        sa.Column('origin', sa.String(length=8), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('screen_id', 'version', name='uq_screen_versions_screen_version'),
    )
    op.create_index('ix_screen_versions_tenant_id', 'screen_versions', ['tenant_id'])
    op.create_index('ix_screen_versions_screen_created', 'screen_versions', ['screen_id', 'created_at'])
    op.add_column('screens', sa.Column('versions_imported_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('screens', 'versions_imported_at')
    op.drop_index('ix_screen_versions_screen_created', table_name='screen_versions')
    op.drop_index('ix_screen_versions_tenant_id', table_name='screen_versions')
    op.drop_table('screen_versions')

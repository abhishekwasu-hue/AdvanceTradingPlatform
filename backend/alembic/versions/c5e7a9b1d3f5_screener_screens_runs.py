"""S1d: screens (saved ScreenQL screens) and screen_runs (append-only run records)

Revision ID: c5e7a9b1d3f5
Revises: a1c3e5f7b9d2
Create Date: 2026-10-11 04:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c5e7a9b1d3f5'
down_revision: Union[str, Sequence[str], None] = 'a1c3e5f7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'screens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('source_text', sa.Text(), nullable=False),
        sa.Column('ast_json', sa.Text(), nullable=False),
        sa.Column('ast_version', sa.String(length=20), nullable=False),
        sa.Column('base_tf', sa.String(length=4), nullable=False),
        sa.Column('params_json', sa.Text(), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_screens_tenant_id'), 'screens', ['tenant_id'], unique=False)
    op.create_table(
        'screen_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('screen_id', sa.Integer(), nullable=True),
        sa.Column('ast_sha256', sa.String(length=64), nullable=False),
        sa.Column('ast_version', sa.String(length=20), nullable=False),
        sa.Column('base_tf', sa.String(length=4), nullable=False),
        sa.Column('universe_json', sa.Text(), nullable=False),
        sa.Column('data_source', sa.String(length=60), nullable=False),
        sa.Column('scanned', sa.Integer(), nullable=False),
        sa.Column('matched', sa.Integer(), nullable=False),
        sa.Column('result_json', sa.Text(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['screen_id'], ['screens.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_screen_runs_tenant_id'), 'screen_runs', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_screen_runs_screen_id'), 'screen_runs', ['screen_id'], unique=False)
    op.create_index(op.f('ix_screen_runs_created_at'), 'screen_runs', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_screen_runs_created_at'), table_name='screen_runs')
    op.drop_index(op.f('ix_screen_runs_screen_id'), table_name='screen_runs')
    op.drop_index(op.f('ix_screen_runs_tenant_id'), table_name='screen_runs')
    op.drop_table('screen_runs')
    op.drop_index(op.f('ix_screens_tenant_id'), table_name='screens')
    op.drop_table('screens')

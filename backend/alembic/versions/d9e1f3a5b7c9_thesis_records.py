"""Phase BD-lite: thesis_records - stored market theses with their shadow multiplier and next-session score

Revision ID: d9e1f3a5b7c9
Revises: c8d0e2f4a6b8
Create Date: 2026-10-06 03:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd9e1f3a5b7c9'
down_revision: Union[str, Sequence[str], None] = 'c8d0e2f4a6b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'thesis_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('confidence', sa.Integer(), nullable=False),
        sa.Column('agreement', sa.Float(), nullable=False),
        sa.Column('shadow_multiplier', sa.Float(), nullable=False),
        sa.Column('last_price', sa.Float(), nullable=True),
        sa.Column('lang', sa.String(length=5), nullable=False),
        sa.Column('narrative_source', sa.String(length=10), nullable=False),
        sa.Column('thesis_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('scored_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('outcome', sa.String(length=10), nullable=True),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('score_detail_json', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_thesis_records_tenant_id'), 'thesis_records', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_thesis_records_day'), 'thesis_records', ['day'], unique=False)
    op.create_index('ix_thesis_records_lookup', 'thesis_records', ['tenant_id', 'symbol', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_thesis_records_lookup', table_name='thesis_records')
    op.drop_index(op.f('ix_thesis_records_day'), table_name='thesis_records')
    op.drop_index(op.f('ix_thesis_records_tenant_id'), table_name='thesis_records')
    op.drop_table('thesis_records')

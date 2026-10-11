"""CH1: chart_drawings - engine-neutral user drawings (time/price anchors), versioned, lockable, soft-deleted

Revision ID: d7f9b1c3e5a7
Revises: f1a2b3c4d5e6
Create Date: 2026-10-11 04:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd7f9b1c3e5a7'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chart_drawings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=40), nullable=False),
        sa.Column('exchange', sa.String(length=10), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('drawing_json', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('locked', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_chart_drawings_tenant_id'), 'chart_drawings', ['tenant_id'], unique=False)
    op.create_index('ix_chart_drawings_user_symbol', 'chart_drawings', ['user_id', 'symbol', 'exchange'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_chart_drawings_user_symbol', table_name='chart_drawings')
    op.drop_index(op.f('ix_chart_drawings_tenant_id'), table_name='chart_drawings')
    op.drop_table('chart_drawings')

"""Z5 / P1-c: `option_strategies` - strategies saved from the Options Builder (instrument inputs and legs as JSON),
shared within the tenant, archived rather than deleted. Behind the `options_builder_saved` flag (off by default).

Revision ID: 5e356061be72
Revises: c5e7a9b1d3f4
Create Date: 2026-10-11 20:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '5e356061be72'
down_revision: Union[str, Sequence[str], None] = 'c5e7a9b1d3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'option_strategies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('underlying', sa.String(length=40), nullable=False),
        sa.Column('strategy_json', sa.Text(), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_option_strategies_tenant_id'), 'option_strategies', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_option_strategies_tenant_id'), table_name='option_strategies')
    op.drop_table('option_strategies')

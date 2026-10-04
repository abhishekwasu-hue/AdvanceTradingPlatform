"""Phase AQ: trader profiles for the AI Copilot's strategy interview

Revision ID: c2e4a6b8d0f1
Revises: b1d3f5a7c9e2
Create Date: 2026-10-04 13:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c2e4a6b8d0f1'
down_revision: Union[str, Sequence[str], None] = 'b1d3f5a7c9e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'trader_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('answers_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('preferences_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index(op.f('ix_trader_profiles_tenant_id'), 'trader_profiles', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_trader_profiles_tenant_id'), table_name='trader_profiles')
    op.drop_table('trader_profiles')

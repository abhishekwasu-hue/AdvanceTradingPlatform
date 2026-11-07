"""P3-4-B10: user_nominees - the one person a user names to use their DPDP rights if they die or cannot act.

Additive: one new table nothing else reads. The previous release ignores it, so a rollback leaves it in place unused.

Revision ID: b3d5f7a9c1e4
Revises: a7d3e9b1c4f6
Create Date: 2026-11-07 10:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b3d5f7a9c1e4'
down_revision: Union[str, Sequence[str], None] = 'a7d3e9b1c4f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - a new table the previous release never reads


def upgrade() -> None:
    op.create_table(
        'user_nominees',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('relationship_to_user', sa.String(60), nullable=False),
        sa.Column('email', sa.String(254), nullable=True),
        sa.Column('phone', sa.String(20), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_user_nominees_tenant_id', 'user_nominees', ['tenant_id'])
    op.create_index('ix_user_nominees_user_id', 'user_nominees', ['user_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_user_nominees_user_id', table_name='user_nominees')
    op.drop_index('ix_user_nominees_tenant_id', table_name='user_nominees')
    op.drop_table('user_nominees')

"""N2-2: experience_opt_outs - a user's logged opt-out of one experience gate for one IST day (flag experience_levels,
off by default).

Revision ID: a4c6e8f0b2d1
Revises: b4e6d8f0a2c3 (exit sweep open-trades index #221, main's head)
Create Date: 2026-10-12 22:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a4c6e8f0b2d1'
down_revision: Union[str, Sequence[str], None] = 'b4e6d8f0a2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive (one new table) - the previous release runs on this schema (code-only rollback ok)


def upgrade() -> None:
    op.create_table(
        'experience_opt_outs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('gate', sa.String(length=32), nullable=False),
        sa.Column('level', sa.String(length=16), nullable=False),
        sa.Column('reason', sa.String(length=300), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'gate', 'day', name='uq_experience_opt_outs_user_gate_day'),
    )
    op.create_index('ix_experience_opt_outs_tenant_id', 'experience_opt_outs', ['tenant_id'])
    op.create_index('ix_experience_opt_outs_user_day', 'experience_opt_outs', ['user_id', 'day'])


def downgrade() -> None:
    op.drop_index('ix_experience_opt_outs_user_day', table_name='experience_opt_outs')
    op.drop_index('ix_experience_opt_outs_tenant_id', table_name='experience_opt_outs')
    op.drop_table('experience_opt_outs')

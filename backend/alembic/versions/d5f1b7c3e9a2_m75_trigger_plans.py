"""M75 W-8 (owner order M75 CW-6): the `trigger_plans` table - one "set and wait" plan per row (condition, his
order(s) and exits, validity, his place-at-once / ask-me-first choice, state).

Revision ID: d5f1b7c3e9a2
Revises: c4e7a9b1d3f5
Create Date: 2026-11-02 09:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'd5f1b7c3e9a2'
down_revision: str | Sequence[str] | None = 'c4e7a9b1d3f5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("a new table only: no existing column, constraint or row changes; the previous release never reads "
                       "or writes trigger_plans, so a code-only rollback leaves it unused and intact")


def upgrade() -> None:
    op.create_table(
        'trigger_plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('client_ref', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=True),
        sa.Column('underlying', sa.String(length=40), nullable=False),
        sa.Column('watch_symbol', sa.String(length=60), nullable=False),
        sa.Column('watch_exchange', sa.String(length=10), nullable=False),
        sa.Column('condition_json', sa.Text(), nullable=False),
        sa.Column('orders_json', sa.Text(), nullable=False),
        sa.Column('approval', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('valid_until', sa.DateTime(timezone=True), nullable=False),
        sa.Column('armed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('triggered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('trigger_price', sa.Float(), nullable=True),
        sa.Column('last_price', sa.Float(), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('results_json', sa.Text(), nullable=True),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'user_id', 'client_ref', name='uq_trigger_plans_user_ref'),
    )
    op.create_index('ix_trigger_plans_tenant_id', 'trigger_plans', ['tenant_id'], unique=False)
    op.create_index('ix_trigger_plans_user_id', 'trigger_plans', ['user_id'], unique=False)
    op.create_index('ix_trigger_plans_tenant_status', 'trigger_plans', ['tenant_id', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_trigger_plans_tenant_status', table_name='trigger_plans')
    op.drop_index('ix_trigger_plans_user_id', table_name='trigger_plans')
    op.drop_index('ix_trigger_plans_tenant_id', table_name='trigger_plans')
    op.drop_table('trigger_plans')

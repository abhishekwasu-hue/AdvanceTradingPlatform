"""AUD-G2.4: activation_events (the first time each person took each activation step; the "already counted" record
behind atp_activation_total{step}). Backfills the steps that existing rows already prove (signup, broker connect, first
paper trade) so a person who took them before this release is not counted again.

Revision ID: d0f0a1b2g204
Revises: d0f0a1b2c3d7
Create Date: 2026-10-29 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd0f0a1b2g204'
down_revision: Union[str, Sequence[str], None] = 'd0f0a1b2c3d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - a new table the previous release never reads


def upgrade() -> None:
    op.create_table(
        'activation_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('step', sa.String(32), nullable=False),
        sa.Column('reached_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'step', name='uq_activation_events_user_step'),
    )
    op.create_index('ix_activation_events_tenant_id', 'activation_events', ['tenant_id'])
    # Backfill what existing rows already prove (first chart and first journal review leave no row behind: those two
    # are recorded from this release on).
    op.execute(
        "INSERT INTO activation_events (tenant_id, user_id, step, reached_at) "
        "SELECT tenant_id, id, 'signup', created_at FROM users WHERE created_at IS NOT NULL"
    )
    op.execute(
        "INSERT INTO activation_events (tenant_id, user_id, step, reached_at) "
        "SELECT u.tenant_id, b.user_id, 'broker_connect', MIN(b.created_at) FROM broker_credentials b "
        "JOIN users u ON u.id = b.user_id WHERE b.created_at IS NOT NULL GROUP BY u.tenant_id, b.user_id"
    )
    op.execute(
        "INSERT INTO activation_events (tenant_id, user_id, step, reached_at) "
        "SELECT u.tenant_id, t.user_id, 'first_paper_trade', MIN(t.entry_time) FROM trades t "
        "JOIN users u ON u.id = t.user_id WHERE t.mode = 'PAPER' GROUP BY u.tenant_id, t.user_id"
    )


def downgrade() -> None:
    op.drop_index('ix_activation_events_tenant_id', table_name='activation_events')
    op.drop_table('activation_events')

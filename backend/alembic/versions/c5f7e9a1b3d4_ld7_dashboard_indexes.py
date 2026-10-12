"""LD-7: two composite indexes for the reads every dashboard page makes - the notification feed (tenant, newest first)
and the proposal list (tenant, status, newest first by id).

Revision ID: c5f7e9a1b3d4
Revises: b4e6d8f0a2c3 (#221 ix_trades_open, itself on main's head f3b5d7a9c1e2)
Create Date: 2026-10-12 21:30:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c5f7e9a1b3d4'
down_revision: Union[str, Sequence[str], None] = 'b4e6d8f0a2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - two indexes no query requires; the previous release runs unchanged on this schema (a code-only
# rollback is fine, the indexes only speed up reads it also makes).
ROLLBACK_SAFE = True


def upgrade() -> None:
    # Plain CREATE INDEX (migrations run in one transaction at start-up, scripts/migrate_guard.py); both tables are small
    # on a Phase 0 host.
    op.create_index('ix_notifications_tenant_created', 'notifications', ['tenant_id', 'created_at'])
    op.create_index('ix_ai_actions_tenant_status_id', 'ai_actions', ['tenant_id', 'status', 'id'])


def downgrade() -> None:
    op.drop_index('ix_ai_actions_tenant_status_id', table_name='ai_actions')
    op.drop_index('ix_notifications_tenant_created', table_name='notifications')

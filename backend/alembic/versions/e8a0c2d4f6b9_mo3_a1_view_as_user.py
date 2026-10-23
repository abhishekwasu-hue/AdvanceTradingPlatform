"""MO3-A1: the admin's read-only "View as user" - view_sessions and view_session_requests

Revision ID: e8a0c2d4f6b9
Revises: b5d7f9a1c3e6 (CP6-15, #423 - chained there for now; re-chained onto the migration block's head at merge)
Create Date: 2026-10-23 16:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e8a0c2d4f6b9'
down_revision: Union[str, Sequence[str], None] = 'b5d7f9a1c3e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - two new tables the previous release never reads or writes (a code-only rollback is fine).
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.create_table(
        'view_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('admin_user_id', sa.Integer(), nullable=True),
        sa.Column('admin_session_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.String(length=300), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_reason', sa.String(length=60), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('request_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('refused_count', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['admin_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['admin_session_id'], ['user_sessions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index(op.f('ix_view_sessions_tenant_id'), 'view_sessions', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_view_sessions_user_id'), 'view_sessions', ['user_id'], unique=False)
    op.create_index(op.f('ix_view_sessions_admin_user_id'), 'view_sessions', ['admin_user_id'], unique=False)
    # One open view per admin: the backstop behind the row lock at start (partial - ended views do not count).
    op.create_index('uq_view_sessions_open_per_admin', 'view_sessions', ['admin_user_id'], unique=True,
                    postgresql_where=sa.text('ended_at IS NULL'), sqlite_where=sa.text('ended_at IS NULL'))

    op.create_table(
        'view_session_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('view_session_id', sa.Integer(), nullable=False),
        sa.Column('method', sa.String(length=10), nullable=False),
        sa.Column('path', sa.String(length=300), nullable=False),
        sa.Column('status', sa.Integer(), nullable=False),
        sa.Column('refused', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['view_session_id'], ['view_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_view_session_requests_view_session_id'), 'view_session_requests', ['view_session_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_view_session_requests_view_session_id'), table_name='view_session_requests')
    op.drop_table('view_session_requests')
    op.drop_index('uq_view_sessions_open_per_admin', table_name='view_sessions')
    op.drop_index(op.f('ix_view_sessions_admin_user_id'), table_name='view_sessions')
    op.drop_index(op.f('ix_view_sessions_user_id'), table_name='view_sessions')
    op.drop_index(op.f('ix_view_sessions_tenant_id'), table_name='view_sessions')
    op.drop_table('view_sessions')

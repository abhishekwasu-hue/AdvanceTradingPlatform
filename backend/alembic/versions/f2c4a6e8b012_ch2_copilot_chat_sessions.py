"""CH-2: copilot_chat_sessions (a person's named Copilot conversations, "New chat") and copilot_chat_turns.session_id.

Additive: a new table and a nullable column. Turns written before (or by an older release after a rollback) keep
session_id NULL and are adopted into an "Earlier conversation" by the application when the person's list is read.

Revision ID: f2c4a6e8b012
Revises: d5f1b7c3e9a2
Create Date: 2026-11-05 14:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'f2c4a6e8b012'
down_revision: Union[str, Sequence[str], None] = 'd5f1b7c3e9a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - a new table and a nullable column the previous release never reads


def upgrade() -> None:
    op.create_table(
        'copilot_chat_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(120), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_copilot_chat_sessions_tenant_id', 'copilot_chat_sessions', ['tenant_id'])
    op.create_index('ix_copilot_chat_sessions_user_id', 'copilot_chat_sessions', ['user_id', 'updated_at'])
    op.add_column('copilot_chat_turns', sa.Column('session_id', sa.Integer(),
                                                  sa.ForeignKey('copilot_chat_sessions.id', ondelete='CASCADE'), nullable=True))
    op.create_index('ix_copilot_chat_turns_session_id', 'copilot_chat_turns', ['session_id'])


def downgrade() -> None:
    op.drop_index('ix_copilot_chat_turns_session_id', table_name='copilot_chat_turns')
    op.drop_column('copilot_chat_turns', 'session_id')
    op.drop_index('ix_copilot_chat_sessions_user_id', table_name='copilot_chat_sessions')
    op.drop_index('ix_copilot_chat_sessions_tenant_id', table_name='copilot_chat_sessions')
    op.drop_table('copilot_chat_sessions')

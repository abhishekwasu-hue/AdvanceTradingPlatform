"""CP-UX CU6: copilot_chat_turns (a person's Copilot conversation, kept across reloads; the newest
COPILOT_CHAT_KEEP turns per person; deletable in Settings -> My data and erased with the account).

Revision ID: e1a2c3d4f506
Revises: d0f0a1b2g204
Create Date: 2026-10-30 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e1a2c3d4f506'
down_revision: Union[str, Sequence[str], None] = 'd0f0a1b2g204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - a new table the previous release never reads


def upgrade() -> None:
    op.create_table(
        'copilot_chat_turns',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_copilot_chat_turns_tenant_id', 'copilot_chat_turns', ['tenant_id'])
    op.create_index('ix_copilot_chat_turns_user_id', 'copilot_chat_turns', ['user_id', 'id'])


def downgrade() -> None:
    op.drop_index('ix_copilot_chat_turns_user_id', table_name='copilot_chat_turns')
    op.drop_index('ix_copilot_chat_turns_tenant_id', table_name='copilot_chat_turns')
    op.drop_table('copilot_chat_turns')

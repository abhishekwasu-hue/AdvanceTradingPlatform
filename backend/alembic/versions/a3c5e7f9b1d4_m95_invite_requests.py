"""M95 PR-3: invite_requests - "Ask for an invite" from the sign-in screen while self_signup is OFF (M95-Q3).

Additive: one new table nothing else reads. The previous release ignores it, so a rollback leaves it in place unused.
No row is written and no stored value changes.

Run after 15:30 IST (owner, DECISIONS 25 Oct).

Revision ID: a3c5e7f9b1d4
Revises: f2b4d6e8a0c1
Create Date: 2026-11-10 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'a3c5e7f9b1d4'
down_revision: Union[str, Sequence[str], None] = 'f2b4d6e8a0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - a new table the previous release never reads


def upgrade() -> None:
    op.create_table(
        'invite_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('note', sa.String(500), nullable=True),
        sa.Column('ip_hash', sa.String(64), nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('handled_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_invite_requests_email', 'invite_requests', ['email'])
    op.create_index('ix_invite_requests_created_at', 'invite_requests', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_invite_requests_created_at', table_name='invite_requests')
    op.drop_index('ix_invite_requests_email', table_name='invite_requests')
    op.drop_table('invite_requests')

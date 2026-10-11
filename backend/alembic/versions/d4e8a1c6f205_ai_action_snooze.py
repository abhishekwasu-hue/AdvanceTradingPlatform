"""PW5: a proposal can be snoozed - ai_actions.snoozed_until (nullable; snooze never extends expires_at).

Revision ID: d4e8a1c6f205
Revises: e9c1a3b5d7f9
Create Date: 2026-10-11 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4e8a1c6f205'
down_revision: Union[str, Sequence[str], None] = 'e9c1a3b5d7f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ai_actions', sa.Column('snoozed_until', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('ai_actions') as batch:
        batch.drop_column('snoozed_until')

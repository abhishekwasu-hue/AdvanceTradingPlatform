"""P0.9: users.ai_language - the language of the AI's answers (the dashboard is English only)

Revision ID: a1c3e5f7b9d2
Revises: f7a9b1c3d5e7
Create Date: 2026-10-08 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1c3e5f7b9d2'
down_revision: Union[str, Sequence[str], None] = 'f7a9b1c3d5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A constant server default: existing rows get "en" without a table rewrite on Postgres 11+.
    op.add_column('users', sa.Column('ai_language', sa.String(length=4), nullable=False, server_default='en'))


def downgrade() -> None:
    op.drop_column('users', 'ai_language')

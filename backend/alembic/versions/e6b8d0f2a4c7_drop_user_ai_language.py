"""EO-4: drop users.ai_language - ATP is English only, the per-user AI answer language was ignored since #273

Revision ID: e6b8d0f2a4c7
Revises: c4e8a2d6f0b1 (U7 LG3 financial_period_versions - main's head)
Create Date: 2026-10-20 02:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e6b8d0f2a4c7'
down_revision: Union[str, Sequence[str], None] = 'c4e8a2d6f0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: a dropped column. The previous release's User model still maps `ai_language`, so its queries on `users` would
# fail on this schema: a code-only rollback past this migration is refused (rollback --restore-db, or run this
# migration's downgrade first, which puts the column back).
ROLLBACK_SAFE = False


def upgrade() -> None:
    op.drop_column('users', 'ai_language')


def downgrade() -> None:
    # The column as a1c3e5f7b9d2 created it: a constant server default, so existing rows get "en" (English, the only
    # value the previous release ever stored after #273) without a table rewrite on Postgres 11+.
    op.add_column('users', sa.Column('ai_language', sa.String(length=4), nullable=False, server_default='en'))

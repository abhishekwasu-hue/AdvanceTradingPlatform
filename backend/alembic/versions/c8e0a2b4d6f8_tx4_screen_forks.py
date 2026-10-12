"""U9 TX4: screens.forked_from / forked_version - a saved screen forked from a pre-built library screen

Revision ID: c8e0a2b4d6f8
Revises: b7d9f1a3c5e7
Create Date: 2026-10-12 23:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c8e0a2b4d6f8'
down_revision: Union[str, Sequence[str], None] = 'b7d9f1a3c5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - two nullable columns; the previous release runs on this schema (code-only rollback ok)


def upgrade() -> None:
    op.add_column('screens', sa.Column('forked_from', sa.String(length=120), nullable=True))
    op.add_column('screens', sa.Column('forked_version', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('screens', 'forked_version')
    op.drop_column('screens', 'forked_from')

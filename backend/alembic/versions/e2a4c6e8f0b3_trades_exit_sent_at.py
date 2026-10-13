"""Re-exit guard: trades.exit_sent_at - when a LIVE position's exit was claimed / sent.

An exit order missing from the broker's book is treated as dead (and a fresh exit goes out) only once it was sent on an
earlier trading day, or a clean reconciliation after it shows the position still open; this column is that "sent" time.

Revision ID: e2a4c6e8f0b3
Revises: d7f9a1c3e5b7 (contributed reference rows tenant scope, main's head)
Create Date: 2026-10-13 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e2a4c6e8f0b3'
down_revision: Union[str, Sequence[str], None] = 'd7f9a1c3e5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - one nullable column the previous release never reads; a code-only rollback runs unchanged on
# this schema.
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.add_column('trades', sa.Column('exit_sent_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('trades', 'exit_sent_at')

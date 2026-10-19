"""Re-exit guard: trades.exit_sent_at, reconciled_open_at, exit_filled_quantity, exit_filled_price.

`exit_sent_at`: when a LIVE position's exit was claimed / sent. `reconciled_open_at`: when a reconciliation last found the
position's symbol still open at the broker with no order working on it - an exit order missing from the broker's book is
treated as over (and a fresh exit goes out) only once this is later than `exit_sent_at`. `exit_filled_quantity` /
`exit_filled_price`: what earlier exits of the position filled before the recorded one, so the booking blends every part.

Revision ID: e2a4c6e8f0b3
Revises: e2a4c6b8d0f1 (company symbol per scope, main's head after #247; re-chained from c9e1f3a5b7d2)
Create Date: 2026-10-13 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e2a4c6e8f0b3'
down_revision: Union[str, Sequence[str], None] = 'e2a4c6b8d0f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - four nullable columns the previous release never reads; a code-only rollback runs unchanged on
# this schema.
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.add_column('trades', sa.Column('exit_sent_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('trades', sa.Column('reconciled_open_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('trades', sa.Column('exit_filled_quantity', sa.Float(), nullable=True))
    op.add_column('trades', sa.Column('exit_filled_price', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('trades', 'exit_filled_price')
    op.drop_column('trades', 'exit_filled_quantity')
    op.drop_column('trades', 'reconciled_open_at')
    op.drop_column('trades', 'exit_sent_at')

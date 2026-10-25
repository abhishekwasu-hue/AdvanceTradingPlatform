"""CX-F1: group_exits.stale_json - the legs a combined exit read at their last traded price

Revision ID: c7e9a1b3d5f2
Revises: b3f5d7e9a1c2 (CX, group_exits)
Create Date: 2026-10-25 09:00:00

One nullable column on group_exits; nothing else changes; no backfill.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c7e9a1b3d5f2'
down_revision: Union[str, Sequence[str], None] = 'b3f5d7e9a1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("one nullable column on group_exits: the previous release never names it, so it runs unchanged on the "
                        "upgraded schema; the downgrade drops the column")


def upgrade() -> None:
    op.add_column('group_exits', sa.Column('stale_json', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('group_exits', 'stale_json')

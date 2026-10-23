"""CP6-15: when the losses-in-a-row pause ends - risk_settings.loss_streak_pause and risk_settings.loss_streak_resumed_at

Revision ID: b5d7f9a1c3e6
Revises: a4c6e8f0b2d4 (WL1 system watchlists - main's head when CP6-15 was built)
Create Date: 2026-10-23 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b5d7f9a1c3e6'
down_revision: Union[str, Sequence[str], None] = 'a4c6e8f0b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - a NOT NULL column with a constant server default and a nullable column; the previous release
# never reads or writes them, so it runs unchanged on this schema (a code-only rollback is fine).
ROLLBACK_SAFE = True


def upgrade() -> None:
    # A constant server default: existing rows get "next_session" without a table rewrite on Postgres 11+.
    op.add_column('risk_settings', sa.Column('loss_streak_pause', sa.String(length=16), nullable=False, server_default='next_session'))
    op.add_column('risk_settings', sa.Column('loss_streak_resumed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('risk_settings', 'loss_streak_resumed_at')
    op.drop_column('risk_settings', 'loss_streak_pause')

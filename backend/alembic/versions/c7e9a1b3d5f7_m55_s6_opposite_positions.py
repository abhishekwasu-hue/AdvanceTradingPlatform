"""M55 S6 (A11): one direction per symbol across an organisation's bots - risk_settings.opposite_positions

Revision ID: c7e9a1b3d5f7
Revises: e1a2c3d4f506 (main's head when M55 S6 was built)
Create Date: 2026-10-30 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c7e9a1b3d5f7'
down_revision: Union[str, Sequence[str], None] = 'e1a2c3d4f506'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - a NOT NULL column with a constant server default; the previous release never reads or writes
# it, so it runs unchanged on this schema (a code-only rollback is fine).
ROLLBACK_SAFE = True


def upgrade() -> None:
    # A constant server default: existing rows get "deny" (the owner's default, M55) without a table rewrite.
    op.add_column('risk_settings', sa.Column('opposite_positions', sa.String(length=8), nullable=False, server_default='deny'))


def downgrade() -> None:
    op.drop_column('risk_settings', 'opposite_positions')

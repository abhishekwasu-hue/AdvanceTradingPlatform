"""S4b-2: alert_rules gains fire_on (bar_close / intrabar)

Revision ID: c5e7b9d1f3a5
Revises: a3c5e7b9d1f3
Create Date: 2026-10-11 05:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c5e7b9d1f3a5'
down_revision: Union[str, Sequence[str], None] = 'a3c5e7b9d1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alert_rules', sa.Column('fire_on', sa.String(length=10), server_default='bar_close', nullable=False))


def downgrade() -> None:
    op.drop_column('alert_rules', 'fire_on')

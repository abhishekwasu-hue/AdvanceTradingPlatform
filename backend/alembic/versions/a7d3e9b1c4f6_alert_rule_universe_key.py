"""FN2-h: alert_rules.universe_key - a screen rule can run on a screener list instead of typed symbols.

Additive: one nullable column. Rules written before (or by an older release after a rollback) keep universe_key NULL and
their universe_json symbols.

Revision ID: a7d3e9b1c4f6
Revises: f2c4a6e8b012
Create Date: 2026-11-06 10:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'a7d3e9b1c4f6'
down_revision: Union[str, Sequence[str], None] = 'f2c4a6e8b012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - a nullable column the previous release never reads


def upgrade() -> None:
    op.add_column('alert_rules', sa.Column('universe_key', sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column('alert_rules', 'universe_key')

"""M94 F10: the level exit of a template deployment - strategy_deployments.target_level

One new nullable column with no default: the level of the TEMPLATE_LEVEL target rule (the session VWAP, the session high /
low, the previous close, the opening range's far side; app/strategy_templates/exits.py LEVELS). NULL = no level target,
exactly as before, on every existing row. No row is written and no stored value changes.

Run after 15:30 IST (owner, DECISIONS 25 Oct).

Revision ID: f2b4d6e8a0c1
Revises: e1a3c5e7f9b2
Create Date: 2026-11-10 16:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'f2b4d6e8a0c1'
down_revision: Union[str, Sequence[str], None] = 'e1a3c5e7f9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - one nullable column with no default; the previous release never reads or writes it (its
# TemplateExits has no such field), so it runs unchanged on this schema (a code-only rollback is fine).
ROLLBACK_SAFE = True

COLUMNS = ('target_level',)


def upgrade() -> None:
    op.add_column('strategy_deployments', sa.Column('target_level', sa.String(length=24), nullable=True))


def downgrade() -> None:
    op.drop_column('strategy_deployments', 'target_level')

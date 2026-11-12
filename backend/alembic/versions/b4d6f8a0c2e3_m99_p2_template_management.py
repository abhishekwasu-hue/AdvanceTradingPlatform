"""M99 P2: the trade-management options of a template deployment - strategy_deployments.management

One new nullable column with no default: the JSON text of the intraday templates' trade-management options (a trail by
swing / ATR / average, no-new-extreme and bars-in-trade exits; app/strategy_templates/management.py). NULL = no options, exactly as before, on every existing row. No row is written and
no stored value changes.

Run after 15:30 IST (owner, DECISIONS 25 Oct). If M99 P7 (b4d6f8a0c2e5, on the same parent) merges first, this revision is
re-chained on it at merge (one head).

Revision ID: b4d6f8a0c2e3
Revises: a3c5e7f9b1d4
Create Date: 2026-11-11 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b4d6f8a0c2e3'
down_revision: str | Sequence[str] | None = 'a3c5e7f9b1d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
# Z11: purely additive - one nullable column with no default; the previous release never reads or writes it (its
# TemplateExits has no such field), so it runs unchanged on this schema (a code-only rollback is fine).
ROLLBACK_SAFE = True

COLUMNS = ('management',)


def upgrade() -> None:
    op.add_column('strategy_deployments', sa.Column('management', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('strategy_deployments', 'management')

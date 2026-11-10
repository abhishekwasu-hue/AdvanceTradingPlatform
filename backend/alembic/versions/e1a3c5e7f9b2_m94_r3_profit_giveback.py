"""M94 R3: the profit give-back stop - risk_settings.giveback_pct and today's peak (day_peak_pnl, day_peak_on, giveback_hit_at)

New nullable columns on risk_settings, NULL = not set: `giveback_pct` (the trader's own % of today's peak profit; the
check "Profit give-back stop" on Settings > Risk limits, OFF until he switches it on), and the stored day peak the check
measures from (app/risk_engine/giveback.py). No row is written and no stored value changes.

Run after 15:30 IST (owner, DECISIONS 25 Oct).

Revision ID: e1a3c5e7f9b2
Revises: d6f8a0c2e4b3
Create Date: 2026-11-10 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'e1a3c5e7f9b2'
down_revision: Union[str, Sequence[str], None] = 'd6f8a0c2e4b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - nullable columns with no default; the previous release never reads or writes them, so it runs
# unchanged on this schema (a code-only rollback is fine).
ROLLBACK_SAFE = True

_NEW = (('giveback_pct', sa.Float()), ('day_peak_pnl', sa.Float()), ('day_peak_on', sa.Date()),
        ('giveback_hit_at', sa.DateTime(timezone=True)))


def upgrade() -> None:
    for name, kind in _NEW:
        op.add_column('risk_settings', sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('risk_settings') as batch:
        for name, _ in reversed(_NEW):
            batch.drop_column(name)

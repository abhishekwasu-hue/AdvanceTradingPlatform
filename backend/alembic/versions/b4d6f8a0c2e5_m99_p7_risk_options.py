"""M99 P7: risk options - risk_settings columns for the step-down after losses in a row, the stagnation flag and the
daily loss lock's mark

New nullable columns on risk_settings, NULL = not set (each option is OFF until the trader switches it on in Settings >
Risk limits, behind the `risk_options` flag): `step_down_losses`, `step_down_size_pct`, `step_down_wins`,
`step_down_reset` (the size step-down after losses in a row), `stagnation_bars`, `stagnation_move_pct`,
`stagnation_bar_minutes` (the information-only flag of a position that is not moving) and `daily_loss_locked_at` (when
the daily loss limit was reached on its trading day, written by the worker cycle; the lock holds for the rest of that
day). No row is written and no stored value changes.

Run after 15:30 IST (owner, DECISIONS 25 Oct).

Revision ID: b4d6f8a0c2e5
Revises: a3c5e7f9b1d4
Create Date: 2026-11-11 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'b4d6f8a0c2e5'
down_revision: Union[str, Sequence[str], None] = 'a3c5e7f9b1d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - nullable columns with no default; the previous release never reads or writes them, so it runs
# unchanged on this schema (a code-only rollback is fine).
ROLLBACK_SAFE = True

_NEW = (('step_down_losses', sa.Integer()), ('step_down_size_pct', sa.Float()), ('step_down_wins', sa.Integer()),
        ('step_down_reset', sa.String(length=16)), ('stagnation_bars', sa.Integer()), ('stagnation_move_pct', sa.Float()),
        ('stagnation_bar_minutes', sa.Integer()), ('daily_loss_locked_at', sa.DateTime(timezone=True)))


def upgrade() -> None:
    for name, kind in _NEW:
        op.add_column('risk_settings', sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('risk_settings') as batch:
        for name, _ in reversed(_NEW):
            batch.drop_column(name)

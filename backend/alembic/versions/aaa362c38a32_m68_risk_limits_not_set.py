"""M68 PR-5 (owner order M68, Risk limits; TS1-051 / 052 / 053 / 054): risk_settings loses its pre-chosen values.

* `loss_streak_pause` (TS1-051) and `opposite_positions` (TS1-052) lose their server defaults (`next_session` / `deny`)
  and become nullable - NULL = "Not set". The pause end is asked when the losses-in-a-row check is switched on with no
  minutes; opposite positions Not set = allowed (owner decision D3, provisional default M68Q-3).
* `min_risk_reward` (TS1-053) becomes nullable and its "not set" sentinel 0 becomes NULL (no behaviour change: both
  read as no minimum).
* new `dd_cut_pct` (TS1-054): how much the first drawdown step cuts the size - the user's own value instead of the fixed
  50 %. A row that already has a first step (`dd_level_1_pct`) gets 50 here, the cut that step meant when it was saved.

TS-6: every existing row KEEPS its stored pause / opposite-positions value (an organisation's saved "deny" stays deny);
only rows made from now on may be NULL. Downgrade fills the NULLs with the old defaults and restores the NOT NULL
columns, drops `dd_cut_pct`.

Revision ID: aaa362c38a32
Revises: 6b9d1f3a5c27
Create Date: 2026-11-01 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'aaa362c38a32'
down_revision: str | Sequence[str] | None = '6b9d1f3a5c27'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = True    # Z11
ROLLBACK_SAFE_REASON = ("the previous release reads a NULL loss_streak_pause as next_session, a NULL opposite_positions as deny "
                        "and a NULL min_risk_reward as no minimum (org_min_rr_read); its ORM fills all three on insert; "
                        "dd_cut_pct is a new nullable column it never reads")

COLUMNS = (('loss_streak_pause', 16, 'next_session'), ('opposite_positions', 8, 'deny'))


def upgrade() -> None:
    with op.batch_alter_table('risk_settings') as batch:
        for name, length, old_default in COLUMNS:
            batch.alter_column(name, existing_type=sa.String(length=length), existing_nullable=False,
                               existing_server_default=old_default, nullable=True, server_default=None)
        batch.alter_column('min_risk_reward', existing_type=sa.Float(), existing_nullable=False, nullable=True)
        batch.add_column(sa.Column('dd_cut_pct', sa.Float(), nullable=True))
    op.execute("UPDATE risk_settings SET min_risk_reward = NULL WHERE min_risk_reward <= 0")
    op.execute("UPDATE risk_settings SET dd_cut_pct = 50 WHERE dd_level_1_pct IS NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE risk_settings SET loss_streak_pause = 'next_session' WHERE loss_streak_pause IS NULL")
    op.execute("UPDATE risk_settings SET opposite_positions = 'deny' WHERE opposite_positions IS NULL")
    op.execute("UPDATE risk_settings SET min_risk_reward = 0 WHERE min_risk_reward IS NULL")
    with op.batch_alter_table('risk_settings') as batch:
        batch.drop_column('dd_cut_pct')
        batch.alter_column('min_risk_reward', existing_type=sa.Float(), existing_nullable=True, nullable=False)
        for name, length, old_default in COLUMNS:
            batch.alter_column(name, existing_type=sa.String(length=length), existing_nullable=True,
                               nullable=False, server_default=old_default)

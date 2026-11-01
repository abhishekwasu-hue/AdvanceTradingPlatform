"""M68 PR-2 (owner order M68, TS1-018 / TS1-024): no pre-chosen size unit or fan-out sizing for new rows.

* `risk_settings.lot_size` loses NOT NULL (it had an ORM-only default 1): NULL = the organisation saved no cash unit of its
  own - a cash share trades in single shares (a fact). It is a unit, never a size.
* `paper_sub_accounts.sizing_mode` / `.sizing_value` lose their server defaults MULTIPLIER / 1 and become nullable: NULL
  = Not set - the user's Trade settings > Position size > Extra paper accounts applies, else the account is skipped with
  "Needs your setting" (the Primary takes the order's own lots).

TS-6: every existing row KEEPS its stored value - no row is rewritten; only rows made from now on may be NULL.
Downgrade restores NOT NULL with the old defaults, filling a NULL (Not set) with that old default only there, so the
previous release reads every row.

Revision ID: b4e6f8a0c2d5
Revises: 8d2f4a6c0e19
Create Date: 2026-11-01 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b4e6f8a0c2d5'
down_revision: str | Sequence[str] | None = '8d2f4a6c0e19'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = False    # Z11: allows NULL and drops two server defaults - the previous release's RiskConfig refuses a NULL lot_size and its fan-out reads a NULL sizing mode; a code-only rollback past it is refused, use --restore-db (or this downgrade)


def upgrade() -> None:
    with op.batch_alter_table('risk_settings') as batch:
        batch.alter_column('lot_size', existing_type=sa.Integer(), existing_nullable=False, nullable=True)
    with op.batch_alter_table('paper_sub_accounts') as batch:
        batch.alter_column('sizing_mode', existing_type=sa.String(length=12), existing_nullable=False,
                           existing_server_default='MULTIPLIER', nullable=True, server_default=None)
        batch.alter_column('sizing_value', existing_type=sa.Float(), existing_nullable=False,
                           existing_server_default='1', nullable=True, server_default=None)


def downgrade() -> None:
    op.execute("UPDATE risk_settings SET lot_size = 1 WHERE lot_size IS NULL")
    op.execute("UPDATE paper_sub_accounts SET sizing_mode = 'MULTIPLIER' WHERE sizing_mode IS NULL")
    op.execute("UPDATE paper_sub_accounts SET sizing_value = 1 WHERE sizing_value IS NULL")
    with op.batch_alter_table('risk_settings') as batch:
        batch.alter_column('lot_size', existing_type=sa.Integer(), existing_nullable=True, nullable=False)
    with op.batch_alter_table('paper_sub_accounts') as batch:
        batch.alter_column('sizing_mode', existing_type=sa.String(length=12), existing_nullable=True,
                           nullable=False, server_default='MULTIPLIER')
        batch.alter_column('sizing_value', existing_type=sa.Float(), existing_nullable=True,
                           nullable=False, server_default='1')

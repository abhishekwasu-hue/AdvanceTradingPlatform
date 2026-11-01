"""M68 PR-6 (owner order M68, TS1-060): strategy_deployments.entry_mode loses its pre-chosen server default (AUTO) and
becomes nullable - NULL = "Not set" (the bot follows its owner's Trade settings > Automation > New entries, and places
no entry while that is Not set too).

TS-6: every existing row KEEPS its stored value - no row is rewritten; only rows made from now on may be NULL.
Downgrade restores the NOT NULL column with its old server default, filling the NULLs (Not set) with that old default
only there, so the previous release reads every row.

Revision ID: 6b9d1f3a5c27
Revises: 8d2f4a6c0e19
Create Date: 2026-11-01 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '6b9d1f3a5c27'
down_revision: str | Sequence[str] | None = '8d2f4a6c0e19'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = False    # Z11: drops a server default and allows NULL - the previous release reads a NULL entry_mode as AUTO (a bot the user never chose for would enter by itself); a code-only rollback past it is refused, use --restore-db (or this downgrade)


def upgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.alter_column('entry_mode', existing_type=sa.String(length=10), existing_nullable=False,
                           existing_server_default='AUTO', nullable=True, server_default=None)


def downgrade() -> None:
    op.execute("UPDATE strategy_deployments SET entry_mode = 'AUTO' WHERE entry_mode IS NULL")
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.alter_column('entry_mode', existing_type=sa.String(length=10), existing_nullable=True,
                           nullable=False, server_default='AUTO')

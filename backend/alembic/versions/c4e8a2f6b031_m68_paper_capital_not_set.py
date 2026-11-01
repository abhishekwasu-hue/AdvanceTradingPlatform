"""M68 PR-7 (owner order M68, TS1-064, owner decision D4 / M68Q-4): paper_accounts.starting_capital becomes nullable -
NULL = "Not set" (a new paper account no longer starts from the platform's PAPER_STARTING_CAPITAL; the first paper order
asks for the capital) - and gains the saved marker `capital_saved` (he chose the value himself).

TS-6: every existing account KEEPS its stored capital (no capital is rewritten), so every balance and P&L stays as it
was. The marker is set where the audit log shows he set the capital himself (`paper_capital_set`), else it stays off.
Downgrade fills a Not set capital with the old platform start value (1,000,000) and restores NOT NULL, so the previous
release reads every row.

Revision ID: c4e8a2f6b031
Revises: 8d2f4a6c0e19
Create Date: 2026-11-01 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c4e8a2f6b031'
down_revision: str | Sequence[str] | None = '8d2f4a6c0e19'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = False    # Z11: allows a NULL starting capital - the previous release reads float(starting_capital) and fails on a NULL; a code-only rollback past it is refused, use --restore-db (or this downgrade)

OLD_START = 1_000_000.0
MONEY = sa.Numeric(18, 2)


def upgrade() -> None:
    with op.batch_alter_table('paper_accounts') as batch:
        batch.alter_column('starting_capital', existing_type=MONEY, existing_nullable=False, nullable=True)
        batch.add_column(sa.Column('capital_saved', sa.Boolean(), nullable=False, server_default=sa.false()))
    accounts = sa.table('paper_accounts', sa.column('user_id', sa.Integer()), sa.column('capital_saved', sa.Boolean()))
    audit = sa.table('audit_logs', sa.column('user_id', sa.Integer()), sa.column('event', sa.String()))
    chose = sa.exists().where(audit.c.user_id == accounts.c.user_id, audit.c.event == 'paper_capital_set')
    op.execute(accounts.update().where(chose).values(capital_saved=sa.true()))


def downgrade() -> None:
    accounts = sa.table('paper_accounts', sa.column('starting_capital', MONEY))
    op.execute(accounts.update().where(accounts.c.starting_capital.is_(None)).values(starting_capital=OLD_START))
    with op.batch_alter_table('paper_accounts') as batch:
        batch.drop_column('capital_saved')
        batch.alter_column('starting_capital', existing_type=MONEY, existing_nullable=True, nullable=False)

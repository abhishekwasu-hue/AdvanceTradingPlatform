"""M68 PR-1 (owner order M68, TS1-007 / TS1-010): strategy_deployments.holding and .order_style lose their pre-chosen
server defaults (INTRADAY / MARKET) and become nullable - NULL = "Not set" (the bot follows the user's own Trade settings
> Order defaults, and places no entry while that is Not set too).

TS-6: every existing row KEEPS its stored value - no row is rewritten; only rows made from now on may be NULL.
Downgrade restores the NOT NULL columns with their old server defaults, filling the NULLs (Not set) with those old
defaults only there, so the previous release reads every row.

Revision ID: 8d2f4a6c0e19
Revises: a7d3e5c9f214
Create Date: 2026-10-31 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '8d2f4a6c0e19'
down_revision: str | Sequence[str] | None = 'a7d3e5c9f214'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = False    # Z11: drops two server defaults and allows NULL - the previous release's DeploymentResponse refuses a NULL holding / order_style; a code-only rollback past it is refused, use --restore-db (or this downgrade)

COLUMNS = (('holding', 10, 'INTRADAY'), ('order_style', 20, 'MARKET'))


def upgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        for name, length, old_default in COLUMNS:
            batch.alter_column(name, existing_type=sa.String(length=length), existing_nullable=False,
                               existing_server_default=old_default, nullable=True, server_default=None)


def downgrade() -> None:
    op.execute("UPDATE strategy_deployments SET holding = 'INTRADAY' WHERE holding IS NULL")
    op.execute("UPDATE strategy_deployments SET order_style = 'MARKET' WHERE order_style IS NULL")
    with op.batch_alter_table('strategy_deployments') as batch:
        for name, length, old_default in COLUMNS:
            batch.alter_column(name, existing_type=sa.String(length=length), existing_nullable=True,
                               nullable=False, server_default=old_default)

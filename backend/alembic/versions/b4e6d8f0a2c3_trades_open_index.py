"""#221: ix_trades_open - a partial index on trades (tenant_id) WHERE exit_time IS NULL, for the worker's per-cycle read
of every organisation's open positions (ADR-0004: exits run whether or not a deployment still trades).

Revision ID: b4e6d8f0a2c3
Revises: f3b5d7a9c1e2 (HB2 history_intraday_days, main's head after #218)
Create Date: 2026-10-12 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b4e6d8f0a2c3'
down_revision: Union[str, Sequence[str], None] = 'f3b5d7a9c1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - one index that no query requires; the previous release runs unchanged on this schema (a
# code-only rollback is fine, the index only speeds up a read it also makes).
ROLLBACK_SAFE = True

_OPEN = "exit_time IS NULL"


def upgrade() -> None:
    # Plain CREATE INDEX (no CONCURRENTLY): migrations run in one transaction at start-up (scripts/migrate_guard.py), and
    # the open-positions part of `trades` is small, so the build takes milliseconds.
    op.create_index('ix_trades_open', 'trades', ['tenant_id'], postgresql_where=sa.text(_OPEN), sqlite_where=sa.text(_OPEN))


def downgrade() -> None:
    op.drop_index('ix_trades_open', table_name='trades')

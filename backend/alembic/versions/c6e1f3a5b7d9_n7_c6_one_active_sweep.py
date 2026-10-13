"""N7 C6 review: one active backtest sweep per organisation, enforced by the database - a partial unique index on
`backtest_sweeps(tenant_id)` over the active statuses (queued / running / finalizing). The application's check-then-insert
let two concurrent requests both queue one; the index refuses the second (the API answers 409). Active duplicates left by
that race (none expected) are failed first - the newest active sweep of each organisation stays - or the index could not
be built.

Revision ID: c6e1f3a5b7d9
Revises: c6d8e0f2a4b6
Create Date: 2026-10-13 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c6e1f3a5b7d9'
down_revision: Union[str, Sequence[str], None] = 'c6d8e0f2a4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: reviewed exception below
ROLLBACK_SAFE_REASON = ("the previous release already refuses a second active sweep per organisation before it inserts one; the "
                        "index only closes the race between two concurrent requests (the older code then gets an IntegrityError "
                        "for a sweep it would have refused anyway), and the clean-up UPDATE only fails duplicate active sweeps "
                        "that race left behind")

_ACTIVE = "status IN ('queued', 'running', 'finalizing')"


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE backtest_sweeps SET status = 'failed', error = 'a second active sweep of this organisation (C6 migration)' "
        "WHERE status IN ('queued', 'running', 'finalizing') AND id NOT IN "
        "(SELECT MAX(id) FROM backtest_sweeps WHERE status IN ('queued', 'running', 'finalizing') GROUP BY tenant_id)"))
    op.execute(sa.text(
        "UPDATE backtest_sweep_tasks SET status = 'failed', error = 'its sweep ended (C6 migration)' "
        "WHERE status IN ('queued', 'running') AND sweep_id IN "
        "(SELECT sweep_id FROM backtest_sweeps WHERE status NOT IN ('queued', 'running', 'finalizing'))"))
    op.create_index('uq_backtest_sweeps_one_active', 'backtest_sweeps', ['tenant_id'], unique=True,
                    postgresql_where=sa.text(_ACTIVE), sqlite_where=sa.text(_ACTIVE))


def downgrade() -> None:
    op.drop_index('uq_backtest_sweeps_one_active', table_name='backtest_sweeps')

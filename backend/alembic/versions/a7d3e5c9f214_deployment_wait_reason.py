"""M55 S7: strategy_deployments.wait_reason (why a bot waits, in plain words for its card), data_failures (market-data
read failures in a row) and waiting_for_data_since (a bot waiting for data resumes by itself) - trading_worker.py.

Revision ID: a7d3e5c9f214
Revises: f9c2a4e6b801
Create Date: 2026-10-30 20:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7d3e5c9f214'
down_revision: str | Sequence[str] | None = 'f9c2a4e6b801'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = True     # Z11: additive - two nullable columns and one NOT NULL column with a server default; old code never reads them


def upgrade() -> None:
    op.add_column('strategy_deployments', sa.Column('wait_reason', sa.String(length=160), nullable=True))
    op.add_column('strategy_deployments', sa.Column('data_failures', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('strategy_deployments', sa.Column('waiting_for_data_since', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.drop_column('waiting_for_data_since')
        batch.drop_column('data_failures')
        batch.drop_column('wait_reason')

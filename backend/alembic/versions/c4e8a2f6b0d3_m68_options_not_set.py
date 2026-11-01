"""M68 PR-4 (owner order M68, TS1-036 / TS1-042): strategy_deployments.spread_width loses its pre-chosen server default
(2 strike steps) and becomes nullable - NULL = "Not set" (a structure that takes a width asks for it; one that takes none
stores none). A new nullable column `ratio_shorts` holds a ratio spread's own sold legs per bought leg (TS1-042: the 1:2
was ATP's choice).

TS-6: every existing row KEEPS its stored width - no width is rewritten. The ratio spreads made before this release were
built 1:2, so exactly those rows get ratio_shorts = 2 (what they already trade); every other row stays NULL.
Downgrade drops ratio_shorts and restores the NOT NULL width with its old server default, filling a NULL (Not set) with 2
only there, so the previous release reads every row.

Revision ID: c4e8a2f6b0d3
Revises: b4e6f8a0c2d5
Create Date: 2026-11-01 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c4e8a2f6b0d3'
down_revision: str | Sequence[str] | None = 'b4e6f8a0c2d5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("the previous release reads spread_width as `spread_width or 2` (routes, worker, basket) and its ORM "
                        "default still writes 2 on insert, so a NULL width (only on structures that take none) is read as "
                        "before; the UPDATE fills only the new ratio_shorts column, which the previous release never reads; after a "
                        "rollback a ratio spread saved with ratio_shorts other than 2 trades 1:2 again (the previous release "
                        "builds every ratio spread 1:2)")


def upgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.alter_column('spread_width', existing_type=sa.Integer(), existing_nullable=False, existing_server_default='2',
                           nullable=True, server_default=None)
        batch.add_column(sa.Column('ratio_shorts', sa.Integer(), nullable=True))
    op.execute("UPDATE strategy_deployments SET ratio_shorts = 2 "
               "WHERE option_strategy IN ('CALL_RATIO_SPREAD', 'PUT_RATIO_SPREAD')")


def downgrade() -> None:
    op.execute("UPDATE strategy_deployments SET spread_width = 2 WHERE spread_width IS NULL")
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.drop_column('ratio_shorts')
        batch.alter_column('spread_width', existing_type=sa.Integer(), existing_nullable=True, nullable=False, server_default='2')

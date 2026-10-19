"""U6 BK5: breakout_fitness_runs - each breakout template's screen fitness as of a date, computed from the shared lake's
daily bars (flag screener_breakouts_fitness, off by default). Reference data: no tenant column.

Revision ID: d4f6a8c0e2b5
Revises: c3d5e7f9a1b2 (N3 S7a ranking systems, #257 - main's head; re-chained from e2a4c6e8f0b3, earlier e2a4c6b8d0f1, d7f9a1c3e5b7, f3b5d7a9c1e2)
Create Date: 2026-10-12 23:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4f6a8c0e2b5'
down_revision: Union[str, Sequence[str], None] = 'c3d5e7f9a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - one new table that no earlier release reads or writes, so the previous release runs unchanged
# on this schema (a code-only rollback is fine; the table is simply ignored).
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.create_table(
        'breakout_fitness_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('template_id', sa.String(length=64), nullable=False),
        sa.Column('universe', sa.String(length=32), nullable=False),
        sa.Column('as_of', sa.Date(), nullable=False),
        sa.Column('signal_tf', sa.String(length=8), nullable=False),
        sa.Column('params_hash', sa.String(length=64), nullable=False),
        sa.Column('symbols', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('signals', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('stats', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('template_id', 'universe', 'as_of', name='uq_breakout_fitness_run'),
    )


def downgrade() -> None:
    op.drop_table('breakout_fitness_runs')

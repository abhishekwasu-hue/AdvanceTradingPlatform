"""M55 S4: runtime_counters (the worker's per-position exit-attempt counters and alert cool-downs, so a restart does
not reset them - app/trading/stop_state.py).

Revision ID: f9c2a4e6b801
Revises: c7e9a1b3d5f7
Create Date: 2026-10-30 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f9c2a4e6b801'
down_revision: str | Sequence[str] | None = 'c7e9a1b3d5f7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = True     # Z11: additive - a new table the previous release never reads


def upgrade() -> None:
    op.create_table(
        'runtime_counters',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=40), nullable=False),
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('name', 'key', name='uq_runtime_counter_name_key'),
    )


def downgrade() -> None:
    op.drop_table('runtime_counters')

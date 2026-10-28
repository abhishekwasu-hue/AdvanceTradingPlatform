"""DEF-0 PR 4: strategy_deployments.underlying_breach_exit (the trader's own underlying-level exit; NULL = off)

Revision ID: d0f0a1b2c3d6
Revises: d0f0a1b2c3d5
Create Date: 2026-10-28 16:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd0f0a1b2c3d6'
down_revision: Union[str, Sequence[str], None] = 'd0f0a1b2c3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - the previous release runs on this schema (code-only rollback ok)


def upgrade() -> None:
    op.add_column('strategy_deployments', sa.Column('underlying_breach_exit', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('strategy_deployments', 'underlying_breach_exit')

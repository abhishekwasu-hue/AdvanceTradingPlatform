"""OI Banner O5: strategy_deployments.oi_gates (opt-in OI entry gates)

Revision ID: e2b4d6f8a0c3
Revises: e6a1b2c3d4f5
Create Date: 2026-10-11 02:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e2b4d6f8a0c3'
down_revision: Union[str, Sequence[str], None] = 'e6a1b2c3d4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('strategy_deployments', sa.Column('oi_gates', sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column('strategy_deployments', 'oi_gates')

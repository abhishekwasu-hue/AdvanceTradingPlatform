"""Phase U: free-form option structures - custom legs on deployments

Revision ID: c5e7a9b2d4f6
Revises: b4d6f8a1c3e5
Create Date: 2026-09-28 17:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c5e7a9b2d4f6'
down_revision: Union[str, Sequence[str], None] = 'b4d6f8a1c3e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.add_column(sa.Column('custom_legs', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.drop_column('custom_legs')

"""add SEBI algo tagging (Phase D1): tenants.algo_id, orders.algo_tag

Revision ID: e7b3c5d1a409
Revises: d6a2b9f4e158
Create Date: 2026-09-26 12:05:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e7b3c5d1a409'
down_revision: Union[str, Sequence[str], None] = 'd6a2b9f4e158'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('algo_id', sa.String(length=32), nullable=True))
    op.add_column('orders', sa.Column('algo_tag', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('orders', 'algo_tag')
    op.drop_column('tenants', 'algo_id')

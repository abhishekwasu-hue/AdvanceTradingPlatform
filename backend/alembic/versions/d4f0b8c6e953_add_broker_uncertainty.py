"""tenants: broker-uncertain flag and last reconciliation time (Phase G1)

Revision ID: d4f0b8c6e953
Revises: c3e9a7b5d842
Create Date: 2026-09-26 19:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4f0b8c6e953'
down_revision: Union[str, Sequence[str], None] = 'c3e9a7b5d842'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenants') as batch:
        batch.add_column(sa.Column('broker_uncertain_since', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('broker_uncertain_reason', sa.String(length=500), nullable=True))
        batch.add_column(sa.Column('last_reconciled_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tenants') as batch:
        for column in ('last_reconciled_at', 'broker_uncertain_reason', 'broker_uncertain_since'):
            batch.drop_column(column)

"""Z10-b (G5): alert outbox reliability - `alert_deliveries.idem_key` (one key per notification x channel x address,
unique; NULL for rows queued before the flag), `lease_until` / `claimed_by` (the worker that claimed a row to send it,
until when). The new states DEAD and SUPPRESSED fit the existing `status` column (String(10)).

Revision ID: c7e2a9d4b1f6
Revises: f3a9c1e7b2d4
Create Date: 2026-10-11 23:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c7e2a9d4b1f6'
down_revision: Union[str, Sequence[str], None] = 'f3a9c1e7b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alert_deliveries', sa.Column('idem_key', sa.String(64), nullable=True))
    op.create_index('ix_alert_deliveries_idem_key', 'alert_deliveries', ['idem_key'], unique=True)
    op.add_column('alert_deliveries', sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('alert_deliveries', sa.Column('claimed_by', sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column('alert_deliveries', 'claimed_by')
    op.drop_column('alert_deliveries', 'lease_until')
    op.drop_index('ix_alert_deliveries_idem_key', table_name='alert_deliveries')
    op.drop_column('alert_deliveries', 'idem_key')

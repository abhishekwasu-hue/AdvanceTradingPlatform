"""Z10-b (G5): alert outbox reliability - `alert_deliveries.idem_key` (one key per notification x channel x address,
unique; NULL for rows queued before the flag), `lease_until` / `claimed_by` (the worker that claimed a row to send it,
until when). The new states DEAD and SUPPRESSED fit the existing `status` column (String(10)).
`alert_events.base_tf` - the rule's timeframe when the event was recorded (NULL for older events), which the stale-data
check at send time judges the bar by.

Revision ID: c7e2a9d4b1f6
Revises: b476873844dd
Create Date: 2026-10-11 23:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c7e2a9d4b1f6'
down_revision: Union[str, Sequence[str], None] = 'b476873844dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = False    # Z10-b: new UNIQUE index (idem_key) on the existing alert_deliveries table - per the Z11 rule; the previous release only writes NULL there


def upgrade() -> None:
    op.add_column('alert_deliveries', sa.Column('idem_key', sa.String(64), nullable=True))
    op.create_index('ix_alert_deliveries_idem_key', 'alert_deliveries', ['idem_key'], unique=True)
    op.add_column('alert_deliveries', sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('alert_deliveries', sa.Column('claimed_by', sa.String(64), nullable=True))
    op.add_column('alert_events', sa.Column('base_tf', sa.String(5), nullable=True))


def downgrade() -> None:
    op.drop_column('alert_events', 'base_tf')
    op.drop_column('alert_deliveries', 'claimed_by')
    op.drop_column('alert_deliveries', 'lease_until')
    op.drop_index('ix_alert_deliveries_idem_key', table_name='alert_deliveries')
    op.drop_column('alert_deliveries', 'idem_key')

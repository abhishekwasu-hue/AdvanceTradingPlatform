"""OC5: the near-month future's last price and open interest on each OI collector slot (`oi_snapshots.future_price`,
`oi_snapshots.future_oi`), for the Option chain "Charts" view (futures price vs futures OI through the session).

Revision ID: a7c9e1f3b5d8
Revises: e6b8d0f2a4c7 (EO-4 - drops users.ai_language; main's head when OC5 merged)
Create Date: 2026-10-22 10:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c9e1f3b5d8'
down_revision: Union[str, Sequence[str], None] = 'e6b8d0f2a4c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - two nullable columns on an existing table. The previous release never reads them and its
# inserts leave them NULL.
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.add_column('oi_snapshots', sa.Column('future_price', sa.Float(), nullable=True))
    op.add_column('oi_snapshots', sa.Column('future_oi', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('oi_snapshots') as batch:
        batch.drop_column('future_oi')
        batch.drop_column('future_price')

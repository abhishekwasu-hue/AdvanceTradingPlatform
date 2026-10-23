"""TM94-3: the Phase W recorder's rows (option_chain_snapshots) belong to the organisation whose own session read them

Revision ID: 3c5e7a9b1d24
Revises: 250d796123b7 (main's head on 23 Oct; #465 adds 250d796123b7 on the same parent - whichever merges second is
         re-chained onto the other at merge time)
Create Date: 2026-10-23 21:00:00

- option_chain_snapshots: a nullable tenant_id (the organisation whose own broker session read the chain; NULL = recorded
  platform-wide before this release, read only by the confirmed owner) with its foreign key and a per-tenant lookup
  index. No backfill: an uploaded row already names its organisation in `source` (`upload:<tenant id>`), which the
  readers keep honouring.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '3c5e7a9b1d24'
down_revision: Union[str, Sequence[str], None] = '250d796123b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("the foreign key is on the new nullable tenant_id column only: the previous release never reads or "
                        "writes it (its inserts leave NULL, which the key accepts); the index is not unique")


def upgrade() -> None:
    op.add_column('option_chain_snapshots', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_option_chain_snapshots_tenant_id', 'option_chain_snapshots', 'tenants', ['tenant_id'], ['id'],
                          ondelete='CASCADE')
    op.create_index('ix_option_chain_snapshots_tenant_lookup', 'option_chain_snapshots', ['tenant_id', 'underlying', 'captured_at'],
                    unique=False)


def downgrade() -> None:
    op.drop_index('ix_option_chain_snapshots_tenant_lookup', table_name='option_chain_snapshots')
    op.drop_constraint('fk_option_chain_snapshots_tenant_id', 'option_chain_snapshots', type_='foreignkey')
    op.drop_column('option_chain_snapshots', 'tenant_id')

"""M45 MA-1: broker_accounts.role (PRIMARY / SECONDARY / NULL) and broker_accounts.group_name

Revision ID: e2a4c6e8f0b1
Revises: c7e9a1b3d5f2 (CX-F1)
Create Date: 2026-10-26 23:00:00

Two nullable columns on broker_accounts; nothing else changes; no backfill (every existing account stays outside
multi-account routing until its owner gives it a role).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e2a4c6e8f0b1'
down_revision: Union[str, Sequence[str], None] = 'c7e9a1b3d5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("two nullable columns on broker_accounts: the previous release never names them, so it runs unchanged "
                        "on the upgraded schema; the downgrade drops them")


def upgrade() -> None:
    op.add_column('broker_accounts', sa.Column('role', sa.String(10), nullable=True))
    op.add_column('broker_accounts', sa.Column('group_name', sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column('broker_accounts', 'group_name')
    op.drop_column('broker_accounts', 'role')

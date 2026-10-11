"""Z3 / BT2: `strategy_deployments.entry_mode` - AUTO (the worker enters on its own, unchanged) or APPROVAL (every entry
signal becomes an ENTRY proposal; only an approved one places a PAPER order). Existing rows are AUTO.
`ai_actions.idempotency_key` - the signal an ENTRY proposal was raised for, unique per organisation (NULL for the
monitoring agent's own proposals).

Revision ID: f3a9c1e7b2d4
Revises: a8c0e2f4b6d8
Create Date: 2026-10-11 21:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f3a9c1e7b2d4'
down_revision: Union[str, Sequence[str], None] = 'a8c0e2f4b6d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('strategy_deployments', sa.Column('entry_mode', sa.String(10), nullable=False, server_default='AUTO'))
    op.add_column('ai_actions', sa.Column('idempotency_key', sa.String(200), nullable=True))
    op.create_unique_constraint('uq_ai_actions_tenant_idempotency_key', 'ai_actions', ['tenant_id', 'idempotency_key'])


def downgrade() -> None:
    op.drop_constraint('uq_ai_actions_tenant_idempotency_key', 'ai_actions', type_='unique')
    op.drop_column('ai_actions', 'idempotency_key')
    op.drop_column('strategy_deployments', 'entry_mode')

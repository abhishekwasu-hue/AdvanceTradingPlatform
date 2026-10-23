"""Strategy templates section 5: exits as settings - the template deployment's stop_source / target_rule and their values

Revision ID: d7f9b1c3e5a8
Revises: b5d7f9a1c3e6 (CP6-15 - chained on it for now; the coordinator re-chains it after LAB-18 at merge, one block)
Create Date: 2026-10-23 16:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd7f9b1c3e5a8'
down_revision: Union[str, Sequence[str], None] = 'b5d7f9a1c3e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - five nullable columns with no default; NULL means "as before" (the template's invalidation as
# the stop, no target), and the previous release never reads or writes them, so it runs unchanged on this schema (a
# code-only rollback is fine).
ROLLBACK_SAFE = True

COLUMNS = ('stop_source', 'target_rule', 'target_r', 'target_base_wave', 'target_ratio')


def upgrade() -> None:
    op.add_column('strategy_deployments', sa.Column('stop_source', sa.String(length=24), nullable=True))
    op.add_column('strategy_deployments', sa.Column('target_rule', sa.String(length=24), nullable=True))
    op.add_column('strategy_deployments', sa.Column('target_r', sa.Float(), nullable=True))
    op.add_column('strategy_deployments', sa.Column('target_base_wave', sa.String(length=16), nullable=True))
    op.add_column('strategy_deployments', sa.Column('target_ratio', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('strategy_deployments', 'target_ratio')
    op.drop_column('strategy_deployments', 'target_base_wave')
    op.drop_column('strategy_deployments', 'target_r')
    op.drop_column('strategy_deployments', 'target_rule')
    op.drop_column('strategy_deployments', 'stop_source')

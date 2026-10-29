"""AUD-T3: deployment_bar_reasons (the "Why no trade" timeline - one reason per closed bar a bot looked at)

Revision ID: d0f0a1b2c3d7
Revises: d0f0a1b2c3d6
Create Date: 2026-10-29 08:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd0f0a1b2c3d7'
down_revision: Union[str, Sequence[str], None] = 'd0f0a1b2c3d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - a new table the previous release never reads


def upgrade() -> None:
    op.create_table(
        'deployment_bar_reasons',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('deployment_id', sa.Integer(), sa.ForeignKey('strategy_deployments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('bar_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('code', sa.String(32), nullable=False),
        sa.Column('detail', sa.String(300), nullable=True),
        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_deployment_bar_reasons_tenant_id', 'deployment_bar_reasons', ['tenant_id'])
    op.create_index('ix_deployment_bar_reasons_dep_seen', 'deployment_bar_reasons', ['deployment_id', 'seen_at'])


def downgrade() -> None:
    op.drop_index('ix_deployment_bar_reasons_dep_seen', table_name='deployment_bar_reasons')
    op.drop_index('ix_deployment_bar_reasons_tenant_id', table_name='deployment_bar_reasons')
    op.drop_table('deployment_bar_reasons')

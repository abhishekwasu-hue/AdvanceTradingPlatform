"""AUD-T8: platform_status_samples (the status page's uptime per market session - one row per worker cycle, once a minute)

Revision ID: d0f0a1b2c3d8
Revises: d0f0a1b2c3d7
Create Date: 2026-10-29 20:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd0f0a1b2c3d8'
down_revision: Union[str, Sequence[str], None] = 'd0f0a1b2c3d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - a new table the previous release never reads


def upgrade() -> None:
    op.create_table(
        'platform_status_samples',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sampled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('stale_skips', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('ix_platform_status_samples_sampled_at', 'platform_status_samples', ['sampled_at'])


def downgrade() -> None:
    op.drop_index('ix_platform_status_samples_sampled_at', table_name='platform_status_samples')
    op.drop_table('platform_status_samples')

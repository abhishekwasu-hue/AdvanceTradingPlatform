"""add tenant webhook_token (TradingView webhook ingestion)

Revision ID: e5f8a2c74b16
Revises: d84e2f6a1b93
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f8a2c74b16'
down_revision: Union[str, Sequence[str], None] = 'd84e2f6a1b93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfills a unique random token for every pre-existing tenant (no extension dependency -
    md5 of a random/clock/id mix is unique enough for a webhook credential and needs nothing
    beyond core Postgres), then makes the column NOT NULL + unique.
    """
    op.add_column('tenants', sa.Column('webhook_token', sa.String(length=64), nullable=True))
    op.execute(
        "UPDATE tenants SET webhook_token = md5(random()::text || clock_timestamp()::text || id::text)"
    )
    op.alter_column('tenants', 'webhook_token', nullable=False)
    op.create_index(op.f('ix_tenants_webhook_token'), 'tenants', ['webhook_token'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_tenants_webhook_token'), table_name='tenants')
    op.drop_column('tenants', 'webhook_token')

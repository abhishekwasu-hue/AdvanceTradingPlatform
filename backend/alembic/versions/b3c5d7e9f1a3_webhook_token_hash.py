"""P0.3 / S13: tenants.webhook_token_hash - the TradingView URL token stored hashed (plaintext column kept, nullable,
until each organisation's first rotation)

Revision ID: b3c5d7e9f1a3
Revises: a2b4c6d8e0f2
Create Date: 2026-10-06 12:10:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b3c5d7e9f1a3'
down_revision: Union[str, Sequence[str], None] = 'a2b4c6d8e0f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenants') as batch_op:
        batch_op.add_column(sa.Column('webhook_token_hash', sa.String(length=64), nullable=True))
        batch_op.alter_column('webhook_token', existing_type=sa.String(length=64), nullable=True)
        batch_op.create_index(op.f('ix_tenants_webhook_token_hash'), ['webhook_token_hash'], unique=True)
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # Backfill: every existing token keeps working through its hash from the first request after the deploy.
        op.execute(sa.text("UPDATE tenants SET webhook_token_hash = encode(sha256(convert_to(webhook_token, 'UTF8')), 'hex') "
                           "WHERE webhook_token IS NOT NULL AND webhook_token_hash IS NULL"))


def downgrade() -> None:
    with op.batch_alter_table('tenants') as batch_op:
        batch_op.drop_index(op.f('ix_tenants_webhook_token_hash'))
        batch_op.drop_column('webhook_token_hash')

"""S3b-2: notification_links (per-user Telegram linking), email_opt_outs (unsubscribe), alert_deliveries.address

Revision ID: f1b3d5e7a9c1
Revises: e9a1c3d5f7b9
Create Date: 2026-10-11 06:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f1b3d5e7a9c1'
down_revision: Union[str, Sequence[str], None] = 'e9a1c3d5f7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alert_deliveries', sa.Column('address', sa.String(length=255), nullable=True))
    op.create_table(
        'notification_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=True),
        sa.Column('code_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('chat_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('linked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code_hash'),
    )
    op.create_index(op.f('ix_notification_links_tenant_id'), 'notification_links', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_notification_links_user_id'), 'notification_links', ['user_id'], unique=False)
    op.create_table(
        'email_opt_outs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('address', sa.String(length=255), nullable=False),
        sa.Column('scope', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'address', 'scope', name='uq_email_opt_outs_tenant_address_scope'),
    )
    op.create_index(op.f('ix_email_opt_outs_tenant_id'), 'email_opt_outs', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_email_opt_outs_tenant_id'), table_name='email_opt_outs')
    op.drop_table('email_opt_outs')
    op.drop_index(op.f('ix_notification_links_user_id'), table_name='notification_links')
    op.drop_index(op.f('ix_notification_links_tenant_id'), table_name='notification_links')
    op.drop_table('notification_links')
    op.drop_column('alert_deliveries', 'address')

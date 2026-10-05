"""Phase BE: Telegram inbound - one-time callback nonces for approve/reject buttons, proposal link on notifications

Revision ID: c8d0e2f4a6b8
Revises: b7c9d1e3f5a7
Create Date: 2026-10-06 00:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c8d0e2f4a6b8'
down_revision: Union[str, Sequence[str], None] = 'b7c9d1e3f5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'telegram_callbacks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('action_id', sa.Integer(), nullable=False),
        sa.Column('nonce', sa.String(length=40), nullable=False),
        sa.Column('decision', sa.String(length=10), nullable=False),
        sa.Column('chat_id', sa.String(length=64), nullable=False),
        sa.Column('signature', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('used_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['action_id'], ['ai_actions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['used_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nonce'),
    )
    op.create_index(op.f('ix_telegram_callbacks_tenant_id'), 'telegram_callbacks', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_telegram_callbacks_action_id'), 'telegram_callbacks', ['action_id'], unique=False)
    with op.batch_alter_table('notifications') as batch:
        batch.add_column(sa.Column('ai_action_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_notifications_ai_action_id', 'ai_actions', ['ai_action_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('notifications') as batch:
        batch.drop_constraint('fk_notifications_ai_action_id', type_='foreignkey')
        batch.drop_column('ai_action_id')
    op.drop_index(op.f('ix_telegram_callbacks_action_id'), table_name='telegram_callbacks')
    op.drop_index(op.f('ix_telegram_callbacks_tenant_id'), table_name='telegram_callbacks')
    op.drop_table('telegram_callbacks')

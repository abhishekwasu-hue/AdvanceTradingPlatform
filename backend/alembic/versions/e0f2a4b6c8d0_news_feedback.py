"""Phase BD-2: news_feedback - members' verdicts on feed items (useful / noise / wrong direction)

Revision ID: e0f2a4b6c8d0
Revises: d9e1f3a5b7c9
Create Date: 2026-10-06 06:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e0f2a4b6c8d0'
down_revision: Union[str, Sequence[str], None] = 'd9e1f3a5b7c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'news_feedback',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('news_event_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('verdict', sa.String(length=16), nullable=False),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['news_event_id'], ['news_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'news_event_id', 'user_id', name='uq_news_feedback_member_item'),
    )
    op.create_index(op.f('ix_news_feedback_tenant_id'), 'news_feedback', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_news_feedback_news_event_id'), 'news_feedback', ['news_event_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_news_feedback_news_event_id'), table_name='news_feedback')
    op.drop_index(op.f('ix_news_feedback_tenant_id'), table_name='news_feedback')
    op.drop_table('news_feedback')

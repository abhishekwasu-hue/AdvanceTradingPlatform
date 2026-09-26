"""add news events

Revision ID: a1c9d3e7f204
Revises: e5f8a2c74b16
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c9d3e7f204'
down_revision: Union[str, Sequence[str], None] = 'e5f8a2c74b16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'news_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('headline', sa.String(length=500), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('event_date', sa.Date(), nullable=False),
        sa.Column('affected_symbols_json', sa.Text(), nullable=False),
        sa.Column('sentiment', sa.String(length=20), nullable=False),
        sa.Column('source_json', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_news_events_category'), 'news_events', ['category'])
    op.create_index(op.f('ix_news_events_event_date'), 'news_events', ['event_date'])


def downgrade() -> None:
    op.drop_index(op.f('ix_news_events_event_date'), table_name='news_events')
    op.drop_index(op.f('ix_news_events_category'), table_name='news_events')
    op.drop_table('news_events')

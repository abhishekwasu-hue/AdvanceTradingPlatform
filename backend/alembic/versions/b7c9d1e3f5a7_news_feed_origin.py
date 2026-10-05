"""Phase BB: live news feed - origin/verification columns on news_events, tenant classifications

Revision ID: b7c9d1e3f5a7
Revises: e4a6c8d0f2b3
Create Date: 2026-10-06 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b7c9d1e3f5a7'
down_revision: Union[str, Sequence[str], None] = 'e4a6c8d0f2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('news_events') as batch:
        batch.add_column(sa.Column('origin', sa.String(length=10), nullable=False, server_default='MANUAL'))
        batch.add_column(sa.Column('source_url', sa.String(length=1000), nullable=True))
        batch.add_column(sa.Column('verified', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column('dedupe_hash', sa.String(length=64), nullable=True))
        batch.add_column(sa.Column('feed_id', sa.String(length=40), nullable=True))
        batch.add_column(sa.Column('published_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('classification_json', sa.Text(), nullable=True))
        batch.create_index('ix_news_events_origin', ['origin'], unique=False)
        batch.create_index('ix_news_events_feed_id', ['feed_id'], unique=False)
        batch.create_unique_constraint('uq_news_events_dedupe_hash', ['dedupe_hash'])
    op.create_table(
        'news_classifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('news_event_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('model', sa.String(length=80), nullable=False),
        sa.Column('severity', sa.Integer(), nullable=False),
        sa.Column('classification_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['news_event_id'], ['news_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'news_event_id', name='uq_news_classification_tenant_item'),
    )
    op.create_index(op.f('ix_news_classifications_tenant_id'), 'news_classifications', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_news_classifications_news_event_id'), 'news_classifications', ['news_event_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_news_classifications_news_event_id'), table_name='news_classifications')
    op.drop_index(op.f('ix_news_classifications_tenant_id'), table_name='news_classifications')
    op.drop_table('news_classifications')
    with op.batch_alter_table('news_events') as batch:
        batch.drop_constraint('uq_news_events_dedupe_hash', type_='unique')
        batch.drop_index('ix_news_events_feed_id')
        batch.drop_index('ix_news_events_origin')
        for col in ('classification_json', 'published_at', 'feed_id', 'dedupe_hash', 'verified', 'source_url', 'origin'):
            batch.drop_column(col)

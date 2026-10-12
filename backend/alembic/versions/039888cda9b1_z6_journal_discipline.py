"""Z6 / P1-e: the discipline engine's tables - `journal_entries` (one auto-captured journal per trade: plan, fills,
stop history, chart references, Greeks/IV at entry, the price range seen while open), `journal_tags` (behaviour tags,
AUTO or USER, SUGGESTED / CONFIRMED / DISMISSED) and `journal_settings` (one trader's thresholds and score weights).
All three are personal (user-scoped) and cascade with their trade / user / tenant.

Revision ID: 039888cda9b1
Revises: 5e356061be72
Create Date: 2026-10-11 21:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '039888cda9b1'
down_revision: Union[str, Sequence[str], None] = '5e356061be72'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - the previous release runs on this schema (code-only rollback ok)


def upgrade() -> None:
    op.create_table(
        'journal_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('trade_id', sa.Integer(), sa.ForeignKey('trades.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('mode', sa.String(10), nullable=False),
        sa.Column('plan_json', sa.Text(), nullable=False),
        sa.Column('fills_json', sa.Text(), nullable=False),
        sa.Column('stop_history_json', sa.Text(), nullable=False),
        sa.Column('chart_entry_json', sa.Text(), nullable=True),
        sa.Column('chart_exit_json', sa.Text(), nullable=True),
        sa.Column('greeks_entry_json', sa.Text(), nullable=True),
        sa.Column('iv_entry', sa.Float(), nullable=True),
        sa.Column('iv_exit', sa.Float(), nullable=True),
        sa.Column('high_price', sa.Numeric(18, 4, asdecimal=False), nullable=True),
        sa.Column('low_price', sa.Numeric(18, 4, asdecimal=False), nullable=True),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('trade_id', name='uq_journal_entries_trade_id'),
    )
    op.create_index('ix_journal_entries_tenant_id', 'journal_entries', ['tenant_id'])
    op.create_index('ix_journal_entries_user_id', 'journal_entries', ['user_id'])

    op.create_table(
        'journal_tags',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('trade_id', sa.Integer(), sa.ForeignKey('trades.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tag', sa.String(30), nullable=False),
        sa.Column('source', sa.String(10), nullable=False),
        sa.Column('status', sa.String(10), nullable=False),
        sa.Column('evidence_json', sa.Text(), nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('trade_id', 'tag', name='uq_journal_tags_trade_tag'),
    )
    op.create_index('ix_journal_tags_tenant_id', 'journal_tags', ['tenant_id'])
    op.create_index('ix_journal_tags_user_id', 'journal_tags', ['user_id'])
    op.create_index('ix_journal_tags_trade_id', 'journal_tags', ['trade_id'])

    op.create_table(
        'journal_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('settings_json', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', name='uq_journal_settings_user_id'),
    )
    op.create_index('ix_journal_settings_tenant_id', 'journal_settings', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_journal_settings_tenant_id', table_name='journal_settings')
    op.drop_table('journal_settings')
    op.drop_index('ix_journal_tags_trade_id', table_name='journal_tags')
    op.drop_index('ix_journal_tags_user_id', table_name='journal_tags')
    op.drop_index('ix_journal_tags_tenant_id', table_name='journal_tags')
    op.drop_table('journal_tags')
    op.drop_index('ix_journal_entries_user_id', table_name='journal_entries')
    op.drop_index('ix_journal_entries_tenant_id', table_name='journal_entries')
    op.drop_table('journal_entries')

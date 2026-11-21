"""P2-8 Journal v2 J2 + J3 (docs/plans/journal-v2-PLAN.md section 3): the trader's own check-in and review answers.

Two new tables, nothing else changes: `journal_checkins` (his 20-second check-in on one journal entry: feelings,
confidence, notes while open, plan followed, mistake, one line; private) and `journal_reflections` (his answer to a
review's reflection question, per week / month). Settings live in the existing `journal_settings` JSON.

After 15:30 IST (owner, DECISIONS 25 Oct).

Revision ID: f4b6d8a0c2e5
Revises: e3a5c7e9b1d4
Create Date: 2026-11-21 15:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'f4b6d8a0c2e5'
down_revision: str | Sequence[str] | None = 'e3a5c7e9b1d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("new tables only: the previous release never reads or writes them, so a code-only rollback leaves "
                        "them unused and intact")


def upgrade() -> None:
    op.create_table('journal_checkins',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('trade_id', sa.Integer(), nullable=True),
    sa.Column('manual_trade_id', sa.Integer(), nullable=True),
    sa.Column('feeling_before', sa.String(length=20), nullable=True),
    sa.Column('confidence', sa.Integer(), nullable=True),
    sa.Column('during_json', sa.Text(), nullable=True),
    sa.Column('feeling_after', sa.String(length=20), nullable=True),
    sa.Column('plan_followed', sa.String(length=7), nullable=True),
    sa.Column('mistake', sa.String(length=30), nullable=True),
    sa.Column('lesson', sa.String(length=300), nullable=True),
    sa.Column('setup_name', sa.String(length=100), nullable=True),
    sa.Column('dismissed_open', sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column('dismissed_close', sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['manual_trade_id'], ['journal_manual_trades.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'trade_id', name='uq_journal_checkin_trade'),
    sa.UniqueConstraint('user_id', 'manual_trade_id', name='uq_journal_checkin_manual'))
    op.create_index('ix_journal_checkins_tenant_id', 'journal_checkins', ['tenant_id'])
    op.create_index('ix_journal_checkins_user_id', 'journal_checkins', ['user_id'])
    op.create_table('journal_reflections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('period', sa.String(length=10), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'period', name='uq_journal_reflection_period'))
    op.create_index('ix_journal_reflections_tenant_id', 'journal_reflections', ['tenant_id'])
    op.create_index('ix_journal_reflections_user_id', 'journal_reflections', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_journal_reflections_user_id', table_name='journal_reflections')
    op.drop_index('ix_journal_reflections_tenant_id', table_name='journal_reflections')
    op.drop_table('journal_reflections')
    op.drop_index('ix_journal_checkins_user_id', table_name='journal_checkins')
    op.drop_index('ix_journal_checkins_tenant_id', table_name='journal_checkins')
    op.drop_table('journal_checkins')

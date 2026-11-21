"""P2-8 Journal v2 J4 + J5 (docs/plans/journal-v2-PLAN.md section 4): "My proof" entries / pins and his own plans.

Two new tables, nothing else changes: `journal_proofs` (his own proof entries and the automatic facts he pinned) and
`journal_plans` (his if-then plans and process goals). The J6 delivery choices live in the existing `user_prefs` rows
(namespace `journal_delivery`).

After 15:30 IST (owner, DECISIONS 25 Oct).

Revision ID: a5c7e9b1d3f6
Revises: f4b6d8a0c2e5
Create Date: 2026-11-21 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'a5c7e9b1d3f6'
down_revision: str | Sequence[str] | None = 'f4b6d8a0c2e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("new tables only: the previous release never reads or writes them, so a code-only rollback leaves "
                        "them unused and intact")


def upgrade() -> None:
    op.create_table('journal_proofs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('fact_key', sa.String(length=80), nullable=False),
    sa.Column('text', sa.String(length=300), nullable=True),
    sa.Column('happened_on', sa.Date(), nullable=True),
    sa.Column('trade_id', sa.Integer(), nullable=True),
    sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'fact_key', name='uq_journal_proof_key'))
    op.create_index('ix_journal_proofs_tenant_id', 'journal_proofs', ['tenant_id'])
    op.create_index('ix_journal_proofs_user_id', 'journal_proofs', ['user_id'])
    op.create_table('journal_plans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('if_text', sa.String(length=200), nullable=True),
    sa.Column('then_text', sa.String(length=300), nullable=True),
    sa.Column('trigger', sa.String(length=10), nullable=False),
    sa.Column('metric', sa.String(length=20), nullable=True),
    sa.Column('target_pct', sa.Integer(), nullable=True),
    sa.Column('period', sa.String(length=5), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_journal_plans_tenant_id', 'journal_plans', ['tenant_id'])
    op.create_index('ix_journal_plans_user_id', 'journal_plans', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_journal_plans_user_id', table_name='journal_plans')
    op.drop_index('ix_journal_plans_tenant_id', table_name='journal_plans')
    op.drop_table('journal_plans')
    op.drop_index('ix_journal_proofs_user_id', table_name='journal_proofs')
    op.drop_index('ix_journal_proofs_tenant_id', table_name='journal_proofs')
    op.drop_table('journal_proofs')

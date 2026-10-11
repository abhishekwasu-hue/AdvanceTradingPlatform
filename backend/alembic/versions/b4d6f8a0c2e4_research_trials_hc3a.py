"""H-C3a: research_trials - the append-only trial ledger of a strategy research study

Revision ID: b4d6f8a0c2e4
Revises: c5e7a9b1d3f5
Create Date: 2026-10-11 08:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b4d6f8a0c2e4'
down_revision: Union[str, Sequence[str], None] = 'c5e7a9b1d3f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'research_trials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('study_id', sa.String(length=36), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('dsl_json', sa.Text(), nullable=False),
        sa.Column('dsl_hash', sa.String(length=64), nullable=False),
        sa.Column('symbol', sa.String(length=40), nullable=False),
        sa.Column('exchange', sa.String(length=10), nullable=False),
        sa.Column('timeframe', sa.String(length=10), nullable=False),
        sa.Column('data_from', sa.DateTime(timezone=True), nullable=True),
        sa.Column('data_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('reason', sa.String(length=300), nullable=True),
        sa.Column('metrics_json', sa.Text(), nullable=False),
        sa.Column('returns_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('study_id', 'seq', name='uq_research_trials_study_seq'),
    )
    op.create_index(op.f('ix_research_trials_tenant_id'), 'research_trials', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_research_trials_study_id'), 'research_trials', ['study_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_research_trials_study_id'), table_name='research_trials')
    op.drop_index(op.f('ix_research_trials_tenant_id'), table_name='research_trials')
    op.drop_table('research_trials')

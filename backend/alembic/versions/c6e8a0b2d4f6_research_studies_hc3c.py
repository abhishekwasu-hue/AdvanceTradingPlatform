"""H-C3c: research_studies - a research study as a queued job (run by the research worker)

Revision ID: c6e8a0b2d4f6
Revises: b4d6f8a0c2e4
Create Date: 2026-10-11 10:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c6e8a0b2d4f6'
down_revision: Union[str, Sequence[str], None] = 'b4d6f8a0c2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'research_studies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('study_id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('idea', sa.Text(), nullable=False),
        sa.Column('symbol', sa.String(length=40), nullable=False),
        sa.Column('exchange', sa.String(length=10), nullable=False),
        sa.Column('timeframe', sa.String(length=10), nullable=False),
        sa.Column('days', sa.Integer(), nullable=False),
        sa.Column('max_drafts', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('error', sa.String(length=300), nullable=True),
        sa.Column('data_source', sa.String(length=60), nullable=True),
        sa.Column('usage_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('study_id'),
    )
    op.create_index(op.f('ix_research_studies_tenant_id'), 'research_studies', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_research_studies_status'), 'research_studies', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_research_studies_status'), table_name='research_studies')
    op.drop_index(op.f('ix_research_studies_tenant_id'), table_name='research_studies')
    op.drop_table('research_studies')

"""U7 LG3: financial_period_versions - the point-in-time, append-only versions of each reported period's figures (what
a screen may read as of a time). No other table changes.

Revision ID: c4e8a2d6f0b1
Revises: f3b5d7a9c1e2 (HB2 history_intraday_days, main's head at 53c54ee; queued migrations re-chain at merge)
Create Date: 2026-10-12 21:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c4e8a2d6f0b1'
down_revision: Union[str, Sequence[str], None] = 'f3b5d7a9c1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - one new table that no earlier release reads or writes, so the previous release runs unchanged
# on this schema (a code-only rollback is fine; the table is simply ignored).
ROLLBACK_SAFE = True


def upgrade() -> None:
    op.create_table(
        'financial_period_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('period_type', sa.String(length=10), nullable=False),
        sa.Column('period_label', sa.String(length=20), nullable=False),
        sa.Column('period_end_date', sa.Date(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('figures_json', sa.Text(), nullable=False),
        sa.Column('publication_date', sa.Date(), nullable=True),
        sa.Column('known_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source_json', sa.Text(), nullable=True),
        sa.UniqueConstraint('company_id', 'period_type', 'period_label', 'version', name='uq_financial_period_version'),
    )
    op.create_index('ix_financial_period_versions_known', 'financial_period_versions', ['company_id', 'known_from'])


def downgrade() -> None:
    op.drop_index('ix_financial_period_versions_known', table_name='financial_period_versions')
    op.drop_table('financial_period_versions')

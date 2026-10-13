"""U7 LG3: financial_period_versions - the point-in-time, append-only versions of each reported period's figures (what
a screen may read as of a time), scoped like `financial_periods` after d7f9a1c3e5b7: `tenant_id` NULL = the shared chain,
set = the chain of the organisation that entered the figures (read by it only). No other table changes.

Revision ID: c4e8a2d6f0b1
Revises: d7f9a1c3e5b7 (contributed reference rows tenant scope, PR #234; was f3b5d7a9c1e2 before that merge)
Create Date: 2026-10-12 21:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c4e8a2d6f0b1'
down_revision: Union[str, Sequence[str], None] = 'd7f9a1c3e5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - one new table that no earlier release reads or writes, so the previous release runs unchanged
# on this schema (a code-only rollback is fine; the table is simply ignored).
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = "one new table no earlier release reads or writes; dropping back to the previous release just ignores it"


def upgrade() -> None:
    op.create_table(
        'financial_period_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
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
        sa.UniqueConstraint('company_id', 'period_type', 'period_label', 'tenant_id', 'version', name='uq_financial_period_version_tenant'),
    )
    # NULLs differ in a unique key: the shared chain (tenant_id NULL) gets its own partial unique index
    op.create_index('uq_financial_period_version_shared', 'financial_period_versions', ['company_id', 'period_type', 'period_label', 'version'],
                    unique=True, postgresql_where=sa.text('tenant_id IS NULL'), sqlite_where=sa.text('tenant_id IS NULL'))
    op.create_index('ix_financial_period_versions_tenant_id', 'financial_period_versions', ['tenant_id'])
    op.create_index('ix_financial_period_versions_known', 'financial_period_versions', ['company_id', 'known_from'])


def downgrade() -> None:
    op.drop_index('ix_financial_period_versions_known', table_name='financial_period_versions')
    op.drop_index('ix_financial_period_versions_tenant_id', table_name='financial_period_versions')
    op.drop_index('uq_financial_period_version_shared', table_name='financial_period_versions')
    op.drop_table('financial_period_versions')

"""Security fix: contributed reference rows belong to the organisation that entered them.

Companies, financial periods, shareholding patterns, corporate actions, qualitative factors, calendar events, sector
metrics and manual news events get a nullable `tenant_id`: NULL = shared reference data (provider-ingested, the news
feed, the platform), set = entered by that organisation and visible to it only. Before this, any trading member of any
organisation could write rows every other organisation read (one organisation could rewrite another's fundamentals).

Existing rows: a row entered by a person (`created_by` set) becomes that person's organisation's row - including
shareholding / announcements a member pulled through the provider refresh (the next refresh stores a shared copy).
Companies stay shared (the symbol is the join key); feed news rows stay shared. `uq_company_period` gains `tenant_id`
so two organisations can each hold their own figures for a period.

Revision ID: d7f9a1c3e5b7
Revises: b9e4c2d7a1f3 (N1 X1 scan Then templates #214, main's head)
Create Date: 2026-10-13 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd7f9a1c3e5b7'
down_revision: Union[str, Sequence[str], None] = 'b9e4c2d7a1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: the previous release runs unchanged on this schema - it never reads or writes the new nullable columns (its
# inserts leave them NULL), the new foreign keys only point those columns at tenants, the backfill writes only the new
# columns, and the widened unique key accepts every row the old key accepted (old code's upsert reads the first row of
# the natural key, as it always did). A code-only rollback re-opens the shared writes, nothing breaks.
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = "new nullable columns only the new code reads; the backfill fills only them; the unique key is widened, not narrowed"

TABLES = ('companies', 'financial_periods', 'shareholding_snapshots', 'corporate_actions', 'qualitative_factors',
          'earnings_calendar_events', 'sector_metrics', 'news_events')


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column('tenant_id', sa.Integer(), nullable=True))
        op.create_foreign_key(f'fk_{table}_tenant_id', table, 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')
        op.create_index(f'ix_{table}_tenant_id', table, ['tenant_id'])
    op.drop_constraint('uq_company_period', 'financial_periods', type_='unique')
    op.create_unique_constraint('uq_company_period_tenant', 'financial_periods', ['company_id', 'period_type', 'period_label', 'tenant_id'])
    # A person's entries become their organisation's own (feed news rows and companies stay shared).
    op.execute("UPDATE financial_periods SET tenant_id = (SELECT users.tenant_id FROM users WHERE users.id = financial_periods.created_by) "
               "WHERE created_by IS NOT NULL")
    op.execute("UPDATE shareholding_snapshots SET tenant_id = (SELECT users.tenant_id FROM users WHERE users.id = shareholding_snapshots.created_by) "
               "WHERE created_by IS NOT NULL")
    op.execute("UPDATE corporate_actions SET tenant_id = (SELECT users.tenant_id FROM users WHERE users.id = corporate_actions.created_by) "
               "WHERE created_by IS NOT NULL")
    op.execute("UPDATE qualitative_factors SET tenant_id = (SELECT users.tenant_id FROM users WHERE users.id = qualitative_factors.created_by) "
               "WHERE created_by IS NOT NULL")
    op.execute("UPDATE earnings_calendar_events SET tenant_id = (SELECT users.tenant_id FROM users "
               "WHERE users.id = earnings_calendar_events.created_by) WHERE created_by IS NOT NULL")
    op.execute("UPDATE sector_metrics SET tenant_id = (SELECT users.tenant_id FROM users WHERE users.id = sector_metrics.created_by) "
               "WHERE created_by IS NOT NULL")
    op.execute("UPDATE news_events SET tenant_id = (SELECT users.tenant_id FROM users WHERE users.id = news_events.created_by) "
               "WHERE created_by IS NOT NULL AND origin = 'MANUAL'")


def downgrade() -> None:
    op.drop_constraint('uq_company_period_tenant', 'financial_periods', type_='unique')
    op.create_unique_constraint('uq_company_period', 'financial_periods', ['company_id', 'period_type', 'period_label'])
    for table in reversed(TABLES):
        op.drop_index(f'ix_{table}_tenant_id', table_name=table)
        op.drop_constraint(f'fk_{table}_tenant_id', table, type_='foreignkey')
        op.drop_column(table, 'tenant_id')

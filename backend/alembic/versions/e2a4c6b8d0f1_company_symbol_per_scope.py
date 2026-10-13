"""Security LOW follow-up of #234: a company symbol is unique per scope; shared financial periods get their own key.

* `companies.symbol` was unique across every organisation, so an organisation's private profile (#234) blocked the shared
  one for that symbol for everybody else ("symbol squatting"), and the bulk refresh resolved it by turning that
  organisation's profile into the shared one. Now: one shared profile per symbol (partial unique index, tenant_id IS
  NULL) and at most one per organisation (unique (symbol, tenant_id)); the plain index on symbol stays for look-ups.
* `financial_periods`: `uq_company_period_tenant` treats NULLs as distinct, so two shared rows for the same period were
  possible; `uq_company_period_shared` (partial, tenant_id IS NULL) closes that. (LG3's c4e8a2d6f0b1 adds the same kind
  of index on `financial_period_versions`, a different table - no duplicate index.)

No data changes: before #234 the symbol and the period key were globally unique, so no row breaks the new keys. The
one-off repair of shared profiles written by members before #234 is an operator command, not part of this migration
(`python -m app.fundamentals.repair_shared`, dry run by default).

Rollback caveats:
* Code-only rollback onto this schema: the previous release reads and writes as before, with one difference - its
  bulk refresh may try to make an organisation's private profile shared while a shared profile of the symbol exists,
  which the partial unique index now refuses (an IntegrityError for that request; no data is changed). Its look-ups
  take the first matching row when an organisation also holds a private profile of a shared symbol.
* `downgrade()` restores the global unique index only when no symbol has more than one profile; otherwise it stops
  with the symbols to resolve first (fold or delete the private profiles) - it never deletes data itself.

Revision ID: e2a4c6b8d0f1
Revises: c9e1f3a5b7d2 (BYOK-V3 vision subjects, #217; re-chained from d7f9a1c3e5b7, #234, when #217 merged first).
LG3's c4e8a2d6f0b1 and BK5's d4f6a8c0e2b5 re-chain onto this one when they merge, so there is one head.
Create Date: 2026-10-13 16:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e2a4c6b8d0f1'
down_revision: Union[str, Sequence[str], None] = 'c9e1f3a5b7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: the unique keys are WIDENED for companies (global -> per scope) and the new shared-period key only restates what
# the previous release's code already keeps (one shared row per period); the previous release runs on this schema - see
# the rollback caveats above.
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("company symbol key widened from global to per scope; the shared-period key matches what the previous release "
                        "already writes; no data rewritten")


def upgrade() -> None:
    op.drop_index('ix_companies_symbol', table_name='companies')
    op.create_index('ix_companies_symbol', 'companies', ['symbol'], unique=False)
    op.create_unique_constraint('uq_companies_symbol_tenant', 'companies', ['symbol', 'tenant_id'])
    op.create_index('uq_companies_symbol_shared', 'companies', ['symbol'], unique=True,
                    postgresql_where=sa.text('tenant_id IS NULL'), sqlite_where=sa.text('tenant_id IS NULL'))
    op.create_index('uq_company_period_shared', 'financial_periods', ['company_id', 'period_type', 'period_label'], unique=True,
                    postgresql_where=sa.text('tenant_id IS NULL'), sqlite_where=sa.text('tenant_id IS NULL'))


def downgrade() -> None:
    dupes = op.get_bind().execute(sa.text("SELECT symbol FROM companies GROUP BY symbol HAVING COUNT(*) > 1 ORDER BY symbol")).scalars().all()
    if dupes:
        raise RuntimeError("cannot restore the global unique company symbol: these symbols have more than one profile (fold or delete the "
                           f"organisations' private profiles first): {', '.join(dupes[:50])}")
    op.drop_index('uq_company_period_shared', table_name='financial_periods')
    op.drop_index('uq_companies_symbol_shared', table_name='companies')
    op.drop_constraint('uq_companies_symbol_tenant', 'companies', type_='unique')
    op.drop_index('ix_companies_symbol', table_name='companies')
    op.create_index('ix_companies_symbol', 'companies', ['symbol'], unique=True)

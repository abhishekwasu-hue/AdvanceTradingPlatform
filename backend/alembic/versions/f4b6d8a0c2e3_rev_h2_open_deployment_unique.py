"""REV-H2 (owner decision D1): a stopped bot no longer blocks a new one on the same strategy + symbol.

`uq_deployment_tenant_strategy_symbol_mode_kind` covered every row, so a STOPPED deployment (terminal, kept as history)
refused a new bot on the same tenant + strategy + symbol + mode + instrument kind forever. It becomes a partial unique
index over the rows that are not STOPPED: two live-or-paused bots on one key are still refused; stopped rows stay.

No data changes.

Rollback caveats:
* Code-only rollback onto this schema: the key is NARROWED (it refuses a subset of what the old key refused), so every
  write the previous release makes still succeeds; the previous release's IntegrityError path still answers a duplicate
  of an open bot with 409.
* `downgrade()` restores the full key only when no key has more than one row (a stopped bot plus a new one on the same
  key); otherwise it stops with the count to resolve first (remove the stopped duplicates) - it never deletes data itself.

Revision ID: f4b6d8a0c2e3
Revises: e1a2c3d4f506
Create Date: 2026-10-30 16:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f4b6d8a0c2e3'
down_revision: Union[str, Sequence[str], None] = 'e1a2c3d4f506'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: the unique key is NARROWED (full key -> rows not STOPPED); the previous release's writes all still pass.
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("deployment unique key narrowed to rows that are not STOPPED - it refuses only what the previous key "
                        "already refused; no data rewritten")

_COLUMNS = ['tenant_id', 'strategy_id', 'symbol', 'mode', 'instrument_kind']
_OLD = 'uq_deployment_tenant_strategy_symbol_mode_kind'
_NEW = 'uq_deployment_open_tenant_strategy_symbol_mode_kind'
_OPEN = "status != 'STOPPED'"


def upgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.drop_constraint(_OLD, type_='unique')
    op.create_index(_NEW, 'strategy_deployments', _COLUMNS, unique=True,
                    postgresql_where=sa.text(_OPEN), sqlite_where=sa.text(_OPEN))


def downgrade() -> None:
    dupes = op.get_bind().execute(sa.text(
        "SELECT COUNT(*) FROM (SELECT tenant_id FROM strategy_deployments "
        "GROUP BY tenant_id, strategy_id, symbol, mode, instrument_kind HAVING COUNT(*) > 1) AS d"
    )).scalar_one()
    if dupes:
        raise RuntimeError(f"cannot restore the full deployment unique key: {dupes} strategy + symbol key(s) have a stopped bot and "
                           "a newer one - remove the stopped duplicates first (Paper bot page, Remove)")
    op.drop_index(_NEW, table_name='strategy_deployments')
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.create_unique_constraint(_OLD, _COLUMNS)

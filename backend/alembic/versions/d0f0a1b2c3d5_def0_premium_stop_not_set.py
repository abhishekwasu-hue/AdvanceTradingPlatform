"""DEF-0 D3 / D10: a single option deployment's premium stop is the trader's own - the built-in one becomes not set

Until DEF-0 a single option deployment saved without a premium stop stored the platform's 30% (bought) / 50% (written)
(app/deployments/routes.py). A stored value equal to that built-in for its side is set to NULL (no premium exit): the
row cannot tell it from one the trader typed, and the owner's default for that is "not set" (DEF-0 decision 2). Every
changed row's id and value are copied to strategy_deployments_def0_backup first; the count is printed. Open trades keep
the premium stop they were entered with.

Run after 15:30 IST (owner, DECISIONS 25 Oct).

Revision ID: d0f0a1b2c3d5
Revises: d0f0a1b2c3d4
Create Date: 2026-10-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0f0a1b2c3d5'
down_revision: Union[str, Sequence[str], None] = 'd0f0a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = False    # Z11: rewrites rows (UPDATE) - use --restore-db past it


def upgrade() -> None:
    op.execute("CREATE TABLE strategy_deployments_def0_backup AS SELECT id, premium_stop_pct FROM strategy_deployments "
               "WHERE option_strategy = 'SINGLE' AND ((option_position = 'BUY' AND premium_stop_pct = 30) "
               "OR (option_position = 'WRITE' AND premium_stop_pct = 50))")
    count = int(op.get_bind().execute(sa.text("SELECT count(*) FROM strategy_deployments_def0_backup")).scalar() or 0)
    op.execute("UPDATE strategy_deployments SET premium_stop_pct = NULL WHERE id IN (SELECT id FROM strategy_deployments_def0_backup)")
    print(f"DEF-0: {count} single option deployment(s) had the built-in premium stop - now not set (kept in strategy_deployments_def0_backup)")


def downgrade() -> None:
    op.execute("UPDATE strategy_deployments SET premium_stop_pct = b.premium_stop_pct FROM strategy_deployments_def0_backup b "
               "WHERE b.id = strategy_deployments.id")
    op.execute("DROP TABLE strategy_deployments_def0_backup")

"""U3 G3: screen_runs keeps what a replay needs - the AST, the parameters, the exchange, the newest bar close read (data_as_of) and
each symbol's last closed bar (SCREENER_SPEC §5: every run stores AST version, universe and data version so the result
can be replayed).

Revision ID: d7f1a3c5e9b2
Revises: d7f9a1c3e5b7 (#234 contributed reference rows tenant scope, main's head at the merge; first cut on b4e6d8f0a2c3)
Create Date: 2026-10-12 23:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd7f1a3c5e9b2'
down_revision: Union[str, Sequence[str], None] = 'd7f9a1c3e5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - five nullable columns the previous release never reads or writes (its inserts leave them NULL,
# which reads as "stored before U3, not replayable"); a code-only rollback is fine.
ROLLBACK_SAFE = True


def upgrade() -> None:
    with op.batch_alter_table('screen_runs') as batch:
        batch.add_column(sa.Column('ast_json', sa.Text(), nullable=True))
        batch.add_column(sa.Column('params_json', sa.Text(), nullable=True))
        batch.add_column(sa.Column('data_as_of', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('bar_times_json', sa.Text(), nullable=True))
        batch.add_column(sa.Column('exchange', sa.String(length=10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('screen_runs') as batch:
        batch.drop_column('exchange')
        batch.drop_column('bar_times_json')
        batch.drop_column('data_as_of')
        batch.drop_column('params_json')
        batch.drop_column('ast_json')

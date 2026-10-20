"""UI2-4a1: per-user UI preferences (`user_prefs`), watchlist rows with an exchange plus list order and version, and
the timeframe a chart drawing was made on.

- `user_prefs`: one row per (tenant, user, namespace) holding that namespace's JSON value and its version.
- `watchlists`: `items_json` (nullable), `position` (nullable), `version` (NOT NULL, server default 1).
- `chart_drawings`: `timeframe` (nullable).

Revision ID: d9f1b3c5e7a2
Revises: c4e8a2d6f0b1 (LG3 financial_period_versions - main's head; #277's e6b8d0f2a4c7 also revises it, so whichever
merges second re-chains)
Create Date: 2026-10-20 10:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd9f1b3c5e7a2'
down_revision: Union[str, Sequence[str], None] = 'c4e8a2d6f0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: purely additive - one new table, nullable columns, and one NOT NULL column with a server default. The previous
# release never reads the new table or columns; its watchlist inserts get version 1 from the server default; a list it
# saves rewrites symbols_json only, and the new code rebuilds the rows from symbols_json when the two disagree.
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("one new table, nullable columns and a NOT NULL column with a server default; the previous release "
                        "neither reads nor needs them, and its inserts fill version from the default")


def upgrade() -> None:
    op.create_table(
        'user_prefs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('namespace', sa.String(length=32), nullable=False),
        sa.Column('value_json', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'user_id', 'namespace', name='uq_user_prefs_tenant_user_namespace'),
    )
    op.create_index('ix_user_prefs_tenant_id', 'user_prefs', ['tenant_id'])
    op.create_index('ix_user_prefs_user_id', 'user_prefs', ['user_id'])
    op.add_column('watchlists', sa.Column('items_json', sa.Text(), nullable=True))
    op.add_column('watchlists', sa.Column('position', sa.Integer(), nullable=True))
    op.add_column('watchlists', sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')))
    op.add_column('chart_drawings', sa.Column('timeframe', sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column('chart_drawings', 'timeframe')
    op.drop_column('watchlists', 'version')
    op.drop_column('watchlists', 'position')
    op.drop_column('watchlists', 'items_json')
    op.drop_index('ix_user_prefs_user_id', table_name='user_prefs')
    op.drop_index('ix_user_prefs_tenant_id', table_name='user_prefs')
    op.drop_table('user_prefs')

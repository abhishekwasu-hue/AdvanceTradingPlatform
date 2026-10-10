"""S4a: alert_rules gains universe_json, exchange, last_bar_at, last_checked_at, last_problem (bar-close engine)

Revision ID: a3c5e7b9d1f3
Revises: f1b3d5e7a9c1
Create Date: 2026-10-11 07:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a3c5e7b9d1f3'
down_revision: Union[str, Sequence[str], None] = 'f1b3d5e7a9c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alert_rules', sa.Column('universe_json', sa.Text(), nullable=True))
    op.add_column('alert_rules', sa.Column('exchange', sa.String(length=10), server_default='NSE', nullable=False))
    op.add_column('alert_rules', sa.Column('last_bar_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('alert_rules', sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('alert_rules', sa.Column('last_problem', sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column('alert_rules', 'last_problem')
    op.drop_column('alert_rules', 'last_checked_at')
    op.drop_column('alert_rules', 'last_bar_at')
    op.drop_column('alert_rules', 'exchange')
    op.drop_column('alert_rules', 'universe_json')

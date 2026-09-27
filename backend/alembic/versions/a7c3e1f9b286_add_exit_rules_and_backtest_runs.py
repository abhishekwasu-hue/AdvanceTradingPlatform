"""exit rules on deployments/trades and backtest run records (Phase J)

Revision ID: a7c3e1f9b286
Revises: f6b2d0e8a175
Create Date: 2026-09-26 22:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c3e1f9b286'
down_revision: Union[str, Sequence[str], None] = 'f6b2d0e8a175'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.add_column(sa.Column('exit_rules', sa.Text(), nullable=True))
    with op.batch_alter_table('trades') as batch:
        batch.add_column(sa.Column('exit_rules', sa.Text(), nullable=True))
        batch.add_column(sa.Column('initial_stop_loss', sa.Float(), nullable=True))
        batch.add_column(sa.Column('best_price', sa.Float(), nullable=True))
    op.create_table(
        'backtest_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('strategy_id', sa.String(length=100), nullable=False),
        sa.Column('strategy_version', sa.Integer(), nullable=True),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('base_timeframe', sa.String(length=10), nullable=False),
        sa.Column('params_json', sa.Text(), nullable=True),
        sa.Column('exit_rules', sa.Text(), nullable=True),
        sa.Column('data_source', sa.String(length=30), nullable=False, server_default='uploaded'),
        sa.Column('bars', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('data_from', sa.DateTime(timezone=True), nullable=True),
        sa.Column('data_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('engine_version', sa.String(length=20), nullable=False, server_default='1'),
        sa.Column('metrics_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_backtest_runs_tenant_id', 'backtest_runs', ['tenant_id'])
    op.create_index('ix_backtest_runs_created_at', 'backtest_runs', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_backtest_runs_created_at', table_name='backtest_runs')
    op.drop_index('ix_backtest_runs_tenant_id', table_name='backtest_runs')
    op.drop_table('backtest_runs')
    with op.batch_alter_table('trades') as batch:
        for column in ('best_price', 'initial_stop_loss', 'exit_rules'):
            batch.drop_column(column)
    with op.batch_alter_table('strategy_deployments') as batch:
        batch.drop_column('exit_rules')

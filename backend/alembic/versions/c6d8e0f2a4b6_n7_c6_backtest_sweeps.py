"""N7 C6: distributed backtest sweeps - `backtest_sweeps` (one queued sweep: a parameter grid x symbols on the lake's
bars) and `backtest_sweep_tasks` (its (combination, symbol) backtests, claimed by any number of workers). Both are
organisation-scoped and cascade with the tenant; tasks cascade with their sweep.

Revision ID: c6d8e0f2a4b6
Revises: b4e6d8f0a2c3
Create Date: 2026-10-13 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c6d8e0f2a4b6'
down_revision: Union[str, Sequence[str], None] = 'b4e6d8f0a2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - two new tables nothing older reads; the previous release runs on this schema


def upgrade() -> None:
    op.create_table(
        'backtest_sweeps',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sweep_id', sa.String(36), nullable=False, unique=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('spec_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False, server_default='queued'),
        sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.String(300), nullable=True),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_backtest_sweeps_tenant_id', 'backtest_sweeps', ['tenant_id'])
    op.create_index('ix_backtest_sweeps_status', 'backtest_sweeps', ['status'])
    op.create_table(
        'backtest_sweep_tasks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sweep_id', sa.String(36), sa.ForeignKey('backtest_sweeps.sweep_id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('idx', sa.Integer(), nullable=False),
        sa.Column('combo', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(40), nullable=False),
        sa.Column('status', sa.String(12), nullable=False, server_default='queued'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('worker', sa.String(80), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('error', sa.String(300), nullable=True),
        sa.UniqueConstraint('sweep_id', 'idx', name='uq_backtest_sweep_tasks_idx'),
    )
    op.create_index('ix_backtest_sweep_tasks_sweep_id', 'backtest_sweep_tasks', ['sweep_id'])
    op.create_index('ix_backtest_sweep_tasks_tenant_id', 'backtest_sweep_tasks', ['tenant_id'])
    op.create_index('ix_backtest_sweep_tasks_status', 'backtest_sweep_tasks', ['status'])


def downgrade() -> None:
    op.drop_table('backtest_sweep_tasks')
    op.drop_table('backtest_sweeps')

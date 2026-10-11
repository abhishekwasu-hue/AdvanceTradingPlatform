"""Z5 / P1-d: `trade_plans` (the plan behind each PAPER ticket, with its sizing, override and cost line), `risk_lockouts`
(a user's daily loss lockout, kept for the rest of the IST day) and `paper_accounts.risk_settings_json` (the user's own
risk-by-default settings; NULL = platform defaults) with `entries_day` / `entries_count` (the day's entry slots). All personal (tenant + user) and cascade with them.

Revision ID: 612f572d7ad2
Revises: a7d3e9f1c5b8
Create Date: 2026-10-12 02:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '612f572d7ad2'
down_revision: Union[str, Sequence[str], None] = 'a7d3e9f1c5b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('paper_accounts', sa.Column('risk_settings_json', sa.Text(), nullable=True))
    op.add_column('paper_accounts', sa.Column('entries_day', sa.Date(), nullable=True))
    op.add_column('paper_accounts', sa.Column('entries_count', sa.Integer(), nullable=False, server_default='0'))

    op.create_table(
        'trade_plans',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mode', sa.String(10), nullable=False),
        sa.Column('symbol', sa.String(50), nullable=False),
        sa.Column('direction', sa.String(10), nullable=False),
        sa.Column('setup', sa.String(30), nullable=False),
        sa.Column('entry', sa.Numeric(18, 4, asdecimal=False), nullable=False),
        sa.Column('stop_loss', sa.Numeric(18, 4, asdecimal=False), nullable=False),
        sa.Column('target', sa.Numeric(18, 4, asdecimal=False), nullable=False),
        sa.Column('reward_risk', sa.Float(), nullable=True),
        sa.Column('time_stop', sa.String(10), nullable=True),
        sa.Column('invalidation', sa.String(300), nullable=True),
        sa.Column('checklist_json', sa.Text(), nullable=False),
        sa.Column('checklist_complete', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('risk_amount', sa.Numeric(18, 2, asdecimal=False), nullable=False),
        sa.Column('risk_cap', sa.Numeric(18, 2, asdecimal=False), nullable=False),
        sa.Column('override', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('override_reason', sa.String(300), nullable=True),
        sa.Column('costs_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('reasons_json', sa.Text(), nullable=False),
        sa.Column('proposal_id', sa.Integer(), sa.ForeignKey('ai_actions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('orders.id', ondelete='SET NULL'), nullable=True),
        sa.Column('trade_id', sa.Integer(), sa.ForeignKey('trades.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_trade_plans_tenant_id', 'trade_plans', ['tenant_id'])
    op.create_index('ix_trade_plans_user_created', 'trade_plans', ['user_id', 'created_at'])

    op.create_table(
        'risk_lockouts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mode', sa.String(10), nullable=False),
        sa.Column('trading_day', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('pnl', sa.Numeric(18, 2, asdecimal=False), nullable=False),
        sa.Column('limit_amount', sa.Numeric(18, 2, asdecimal=False), nullable=False),
        sa.Column('reason', sa.String(300), nullable=False),
        sa.Column('locked_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'mode', 'trading_day', 'kind', name='uq_risk_lockouts_user_day_kind'),
    )
    op.create_index('ix_risk_lockouts_tenant_id', 'risk_lockouts', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_risk_lockouts_tenant_id', table_name='risk_lockouts')
    op.drop_table('risk_lockouts')
    op.drop_index('ix_trade_plans_user_created', table_name='trade_plans')
    op.drop_index('ix_trade_plans_tenant_id', table_name='trade_plans')
    op.drop_table('trade_plans')
    op.drop_column('paper_accounts', 'entries_count')
    op.drop_column('paper_accounts', 'entries_day')
    op.drop_column('paper_accounts', 'risk_settings_json')

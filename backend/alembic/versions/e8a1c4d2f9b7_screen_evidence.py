"""U8 EV1/EV2: screen_evidence - the evidence pack (evidence/1) of a matched symbol, stored with its screen run or alert
event (flag evidence_charts, off by default).

Also the merge point of the two heads this branch carries: Z7a-2's market zones (b476873844dd) and main's Z3-a
entry mode (f3a9c1e7b2d4), both on 039888cda9b1. When Z7a-2 is re-chained onto f3a9c1e7b2d4 before it merges, this
down_revision becomes 'b476873844dd' alone (OPEN_QUESTIONS EV-10).

Revision ID: e8a1c4d2f9b7
Revises: b476873844dd, f3a9c1e7b2d4
Create Date: 2026-10-11 23:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e8a1c4d2f9b7'
down_revision: Union[str, Sequence[str], None] = ('b476873844dd', 'f3a9c1e7b2d4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive (one new table) - the previous release runs on this schema (code-only rollback ok)


def upgrade() -> None:
    op.create_table(
        'screen_evidence',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('screen_runs.id', ondelete='CASCADE'), nullable=True),
        sa.Column('alert_event_id', sa.Integer(), sa.ForeignKey('alert_events.id', ondelete='CASCADE'), nullable=True),
        sa.Column('symbol', sa.String(length=40), nullable=False),
        sa.Column('base_tf', sa.String(length=4), nullable=False),
        sa.Column('bar_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('schema', sa.String(length=20), nullable=False, server_default='evidence/1'),
        sa.Column('pack_json', sa.Text(), nullable=False),
        sa.Column('pack_sha256', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('run_id', 'symbol', name='uq_screen_evidence_run_symbol'),
        sa.UniqueConstraint('alert_event_id', name='uq_screen_evidence_alert_event'),
    )
    op.create_index('ix_screen_evidence_tenant_id', 'screen_evidence', ['tenant_id'])
    op.create_index('ix_screen_evidence_user_id', 'screen_evidence', ['user_id'])
    op.create_index('ix_screen_evidence_run_id', 'screen_evidence', ['run_id'])
    op.create_index('ix_screen_evidence_created_at', 'screen_evidence', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_screen_evidence_created_at', table_name='screen_evidence')
    op.drop_index('ix_screen_evidence_run_id', table_name='screen_evidence')
    op.drop_index('ix_screen_evidence_user_id', table_name='screen_evidence')
    op.drop_index('ix_screen_evidence_tenant_id', table_name='screen_evidence')
    op.drop_table('screen_evidence')

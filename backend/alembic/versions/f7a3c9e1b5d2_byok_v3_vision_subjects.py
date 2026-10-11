"""BYOK-V3: `vision_reports` gets a subject kind - proposal (V2), scan_alert (one screen-alert notification of a rule the
user created) or daily_review (one symbol of the user's own after-close review) - and a per-user unique subject key.
`ai_action_id` becomes nullable (only proposals have one). Existing rows are proposals: their key is backfilled.

Revision ID: f7a3c9e1b5d2
Revises: e4c2a9b7d1f6
Create Date: 2026-10-11 21:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f7a3c9e1b5d2'
down_revision: Union[str, Sequence[str], None] = 'e4c2a9b7d1f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('vision_reports') as batch:
        batch.alter_column('ai_action_id', existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column('subject_kind', sa.String(16), nullable=False, server_default='proposal'))
        batch.add_column(sa.Column('subject_key', sa.String(80), nullable=True))
        batch.add_column(sa.Column('notification_id', sa.Integer(), sa.ForeignKey('notifications.id', name='fk_vision_reports_notification_id',
                                                                                  ondelete='SET NULL'), nullable=True))
        batch.add_column(sa.Column('alert_rule_id', sa.Integer(), sa.ForeignKey('alert_rules.id', name='fk_vision_reports_alert_rule_id',
                                                                                ondelete='SET NULL'), nullable=True))
        batch.add_column(sa.Column('symbol', sa.String(50), nullable=True))
        batch.add_column(sa.Column('exchange', sa.String(10), nullable=True))
        batch.add_column(sa.Column('timeframe', sa.String(10), nullable=True))
        batch.add_column(sa.Column('subject_date', sa.Date(), nullable=True))
    op.execute("UPDATE vision_reports SET subject_key = 'proposal:' || CAST(ai_action_id AS VARCHAR) WHERE ai_action_id IS NOT NULL")
    with op.batch_alter_table('vision_reports') as batch:
        batch.create_unique_constraint('uq_vision_reports_user_subject', ['user_id', 'subject_key'])
    op.create_index('ix_vision_reports_subject_kind', 'vision_reports', ['subject_kind'])
    op.create_index('ix_vision_reports_notification_id', 'vision_reports', ['notification_id'])


def downgrade() -> None:
    # Only proposal reports fit the V2 table (ai_action_id NOT NULL): the scan-alert and daily-review rows go.
    op.execute("DELETE FROM vision_reports WHERE ai_action_id IS NULL")
    op.drop_index('ix_vision_reports_notification_id', table_name='vision_reports')
    op.drop_index('ix_vision_reports_subject_kind', table_name='vision_reports')
    with op.batch_alter_table('vision_reports') as batch:
        batch.drop_constraint('uq_vision_reports_user_subject', type_='unique')
        batch.drop_constraint('fk_vision_reports_alert_rule_id', type_='foreignkey')
        batch.drop_constraint('fk_vision_reports_notification_id', type_='foreignkey')
        for column in ('subject_date', 'timeframe', 'exchange', 'symbol', 'alert_rule_id', 'notification_id', 'subject_key', 'subject_kind'):
            batch.drop_column(column)
        batch.alter_column('ai_action_id', existing_type=sa.Integer(), nullable=False)

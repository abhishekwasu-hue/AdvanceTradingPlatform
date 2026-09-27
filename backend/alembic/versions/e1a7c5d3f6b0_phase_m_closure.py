"""Phase M closure: platform controls, incidents, per-user trading disable, trade journal fields

Revision ID: e1a7c5d3f6b0
Revises: d0f6b4c2e5a9
Create Date: 2026-09-27 02:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e1a7c5d3f6b0'
down_revision: Union[str, Sequence[str], None] = 'd0f6b4c2e5a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('trading_disabled_reason', sa.String(length=200), nullable=True))
    with op.batch_alter_table('trades') as batch:
        batch.add_column(sa.Column('regime_at_entry', sa.String(length=20), nullable=True))
        batch.add_column(sa.Column('notes', sa.Text(), nullable=True))
        batch.add_column(sa.Column('tags', sa.String(length=200), nullable=True))
    op.create_table(
        'platform_controls',
        sa.Column('key', sa.String(length=40), primary_key=True),
        sa.Column('value_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'incidents',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('severity', sa.String(length=10), nullable=False, server_default='WARNING'),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='OPEN'),
        sa.Column('source', sa.String(length=30), nullable=False, server_default='operator'),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='SET NULL'), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('mitigated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('root_cause', sa.Text(), nullable=True),
        sa.Column('actions_taken', sa.Text(), nullable=True),
        sa.Column('audit_log_from_id', sa.Integer(), nullable=True),
        sa.Column('audit_log_to_id', sa.Integer(), nullable=True),
        sa.Column('data_loss_minutes', sa.Float(), nullable=True),
        sa.Column('downtime_minutes', sa.Float(), nullable=True),
        sa.Column('opened_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_incidents_status', 'incidents', ['status'])


def downgrade() -> None:
    op.drop_index('ix_incidents_status', table_name='incidents')
    op.drop_table('incidents')
    op.drop_table('platform_controls')
    with op.batch_alter_table('trades') as batch:
        batch.drop_column('tags')
        batch.drop_column('notes')
        batch.drop_column('regime_at_entry')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('trading_disabled_reason')

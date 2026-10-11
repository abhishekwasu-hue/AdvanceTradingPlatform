"""Part D4: registered static egress IPs per broker (PRIMARY / BACKUP) and their change history

Revision ID: d4e1f2a3b4c5
Revises: e2b4d6f8a0c1
Create Date: 2026-10-10 23:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'e2b4d6f8a0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'egress_ips',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('broker_name', sa.String(length=50), nullable=False),
        sa.Column('role', sa.String(length=10), nullable=False, server_default='PRIMARY'),
        sa.Column('ip', sa.String(length=45), nullable=False),
        sa.Column('registered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'broker_name', 'role', name='uq_egress_ip_role'),
    )
    op.create_index('ix_egress_ips_tenant_id', 'egress_ips', ['tenant_id'])
    op.create_index('ix_egress_ips_ip', 'egress_ips', ['ip'])
    op.create_table(
        'egress_ip_changes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('broker_name', sa.String(length=50), nullable=False),
        sa.Column('role', sa.String(length=10), nullable=False),
        sa.Column('old_ip', sa.String(length=45), nullable=True),
        sa.Column('new_ip', sa.String(length=45), nullable=False),
        sa.Column('changed_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_egress_ip_changes_tenant_id', 'egress_ip_changes', ['tenant_id'])
    op.create_index('ix_egress_ip_changes_changed_at', 'egress_ip_changes', ['changed_at'])


def downgrade() -> None:
    op.drop_index('ix_egress_ip_changes_changed_at', table_name='egress_ip_changes')
    op.drop_index('ix_egress_ip_changes_tenant_id', table_name='egress_ip_changes')
    op.drop_table('egress_ip_changes')
    op.drop_index('ix_egress_ips_ip', table_name='egress_ips')
    op.drop_index('ix_egress_ips_tenant_id', table_name='egress_ips')
    op.drop_table('egress_ips')

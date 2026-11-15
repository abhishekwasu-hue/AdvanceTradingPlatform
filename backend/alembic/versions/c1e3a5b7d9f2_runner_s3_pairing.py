"""Runner S3 (docs/design/USER_RUNNER.md section 4; flag `user_runner`, OFF): the ATP side of the user's own runner

Five new tables, nothing else changes: `runner_devices` (each user's paired runner devices: name, Ed25519 public key,
status, what the device last reported in its signed heartbeat), `runner_pairing_codes` (a one-time code, stored hashed,
short expiry, single use) and the read-only mirrors `runner_mirror_orders`, `runner_mirror_positions`,
`runner_mirror_audit` (what the runner uploads; keyed by device). No column holds a broker key, secret or token.

Any time before practice starts; after 15:30 IST once it has (owner, DECISIONS 25 Oct).

Revision ID: c1e3a5b7d9f2
Revises: b4d6f8a0c2e5
Create Date: 2026-11-14 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'c1e3a5b7d9f2'
down_revision: Union[str, Sequence[str], None] = 'b4d6f8a0c2e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("new tables only: no existing column, constraint or row changes; the previous release never reads "
                        "or writes them, so a code-only rollback leaves them unused and intact")


def upgrade() -> None:
    op.create_table('runner_devices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('device_uid', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('public_key', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('version', sa.String(length=40), nullable=True),
    sa.Column('paired_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('run_state', sa.String(length=10), nullable=True),
    sa.Column('kill_switch_on', sa.Boolean(), nullable=True),
    sa.Column('open_positions', sa.Integer(), nullable=True),
    sa.Column('broker_session', sa.String(length=10), nullable=True),
    sa.Column('egress_ip', sa.String(length=45), nullable=True),
    sa.Column('last_signed_ms', sa.BigInteger(), nullable=False),
    sa.Column('offline_alerted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('device_uid')
    )
    op.create_index(op.f('ix_runner_devices_tenant_id'), 'runner_devices', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_runner_devices_user_id'), 'runner_devices', ['user_id'], unique=False)
    op.create_table('runner_mirror_audit',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.Integer(), nullable=False),
    sa.Column('seq', sa.BigInteger(), nullable=False),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('summary', sa.String(length=300), nullable=False),
    sa.Column('row_hash', sa.String(length=64), nullable=False),
    sa.Column('prev_hash', sa.String(length=64), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['runner_devices.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('device_id', 'seq', name='uq_runner_mirror_audit_seq')
    )
    op.create_index(op.f('ix_runner_mirror_audit_device_id'), 'runner_mirror_audit', ['device_id'], unique=False)
    op.create_index(op.f('ix_runner_mirror_audit_tenant_id'), 'runner_mirror_audit', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_runner_mirror_audit_user_id'), 'runner_mirror_audit', ['user_id'], unique=False)
    op.create_table('runner_mirror_orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.Integer(), nullable=False),
    sa.Column('runner_ref', sa.String(length=64), nullable=False),
    sa.Column('deployment_ref', sa.String(length=64), nullable=True),
    sa.Column('mode', sa.String(length=5), nullable=False),
    sa.Column('symbol', sa.String(length=60), nullable=False),
    sa.Column('exchange', sa.String(length=10), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('order_type', sa.String(length=12), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('price', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=True),
    sa.Column('trigger_price', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('filled_quantity', sa.Float(), nullable=True),
    sa.Column('average_price', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=True),
    sa.Column('reason', sa.String(length=300), nullable=True),
    sa.Column('placed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['runner_devices.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('device_id', 'runner_ref', name='uq_runner_mirror_orders_ref')
    )
    op.create_index(op.f('ix_runner_mirror_orders_device_id'), 'runner_mirror_orders', ['device_id'], unique=False)
    op.create_index(op.f('ix_runner_mirror_orders_tenant_id'), 'runner_mirror_orders', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_runner_mirror_orders_user_id'), 'runner_mirror_orders', ['user_id'], unique=False)
    op.create_table('runner_mirror_positions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.Integer(), nullable=False),
    sa.Column('runner_ref', sa.String(length=64), nullable=False),
    sa.Column('deployment_ref', sa.String(length=64), nullable=True),
    sa.Column('mode', sa.String(length=5), nullable=False),
    sa.Column('symbol', sa.String(length=60), nullable=False),
    sa.Column('exchange', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('average_price', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=True),
    sa.Column('last_price', sa.Numeric(precision=18, scale=4, asdecimal=False), nullable=True),
    sa.Column('unrealised_pnl', sa.Numeric(precision=18, scale=2, asdecimal=False), nullable=True),
    sa.Column('realised_pnl', sa.Numeric(precision=18, scale=2, asdecimal=False), nullable=True),
    sa.Column('exit_reason', sa.String(length=120), nullable=True),
    sa.Column('opened_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['runner_devices.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('device_id', 'runner_ref', name='uq_runner_mirror_positions_ref')
    )
    op.create_index(op.f('ix_runner_mirror_positions_device_id'), 'runner_mirror_positions', ['device_id'], unique=False)
    op.create_index(op.f('ix_runner_mirror_positions_tenant_id'), 'runner_mirror_positions', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_runner_mirror_positions_user_id'), 'runner_mirror_positions', ['user_id'], unique=False)
    op.create_table('runner_pairing_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('device_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['runner_devices.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code_hash')
    )
    op.create_index(op.f('ix_runner_pairing_codes_tenant_id'), 'runner_pairing_codes', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_runner_pairing_codes_user_id'), 'runner_pairing_codes', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_runner_pairing_codes_user_id'), table_name='runner_pairing_codes')
    op.drop_index(op.f('ix_runner_pairing_codes_tenant_id'), table_name='runner_pairing_codes')
    op.drop_table('runner_pairing_codes')
    op.drop_index(op.f('ix_runner_mirror_positions_user_id'), table_name='runner_mirror_positions')
    op.drop_index(op.f('ix_runner_mirror_positions_tenant_id'), table_name='runner_mirror_positions')
    op.drop_index(op.f('ix_runner_mirror_positions_device_id'), table_name='runner_mirror_positions')
    op.drop_table('runner_mirror_positions')
    op.drop_index(op.f('ix_runner_mirror_orders_user_id'), table_name='runner_mirror_orders')
    op.drop_index(op.f('ix_runner_mirror_orders_tenant_id'), table_name='runner_mirror_orders')
    op.drop_index(op.f('ix_runner_mirror_orders_device_id'), table_name='runner_mirror_orders')
    op.drop_table('runner_mirror_orders')
    op.drop_index(op.f('ix_runner_mirror_audit_user_id'), table_name='runner_mirror_audit')
    op.drop_index(op.f('ix_runner_mirror_audit_tenant_id'), table_name='runner_mirror_audit')
    op.drop_index(op.f('ix_runner_mirror_audit_device_id'), table_name='runner_mirror_audit')
    op.drop_table('runner_mirror_audit')
    op.drop_index(op.f('ix_runner_devices_user_id'), table_name='runner_devices')
    op.drop_index(op.f('ix_runner_devices_tenant_id'), table_name='runner_devices')
    op.drop_table('runner_devices')

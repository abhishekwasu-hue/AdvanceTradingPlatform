"""Runner S4 (docs/design/USER_RUNNER.md section 4; flag `user_runner`, OFF): the user's own commands for his runner

One new table and one new column, nothing else changes: `runner_commands` (a command created only by the user's own
action on his own runner - today his kill switch ON / OFF, rule R-1 - signed by ATP's command key and acked by the
runner) and `runner_devices.broker_login` (the runner's daily broker login line as it reports it - never a value). No
column holds a broker key, secret or token.

Any time before practice starts; after 15:30 IST once it has (owner, DECISIONS 25 Oct).

Revision ID: d2f4b6c8e0a1
Revises: c1e3a5b7d9f2
Create Date: 2026-11-14 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'd2f4b6c8e0a1'
down_revision: Union[str, Sequence[str], None] = 'c1e3a5b7d9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("a new table and a new nullable column only: no existing column, constraint or row changes; the "
                        "previous release never reads or writes them, so a code-only rollback leaves them unused and intact")


def upgrade() -> None:
    op.create_table('runner_commands',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('payload_json', sa.Text(), nullable=False),
    sa.Column('signature', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('acked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['device_id'], ['runner_devices.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_runner_commands_device_id'), 'runner_commands', ['device_id'], unique=False)
    op.create_index(op.f('ix_runner_commands_tenant_id'), 'runner_commands', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_runner_commands_user_id'), 'runner_commands', ['user_id'], unique=False)
    op.add_column('runner_devices', sa.Column('broker_login', sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column('runner_devices', 'broker_login')
    op.drop_index(op.f('ix_runner_commands_user_id'), table_name='runner_commands')
    op.drop_index(op.f('ix_runner_commands_tenant_id'), table_name='runner_commands')
    op.drop_index(op.f('ix_runner_commands_device_id'), table_name='runner_commands')
    op.drop_table('runner_commands')

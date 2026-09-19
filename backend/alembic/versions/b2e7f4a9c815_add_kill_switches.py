"""add kill switches

Revision ID: b2e7f4a9c815
Revises: 9a3c6e1b2d47
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2e7f4a9c815'
down_revision: Union[str, Sequence[str], None] = '9a3c6e1b2d47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'kill_switches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('scope', sa.String(length=20), nullable=False),
        sa.Column('strategy_id', sa.String(length=100), nullable=True),
        sa.Column('engaged', sa.Boolean(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('engaged_by', sa.Integer(), nullable=True),
        sa.Column('engaged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('disengaged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['engaged_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'scope', 'strategy_id', name='uq_kill_switch_scope'),
    )
    op.create_index(op.f('ix_kill_switches_tenant_id'), 'kill_switches', ['tenant_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_kill_switches_tenant_id'), table_name='kill_switches')
    op.drop_table('kill_switches')

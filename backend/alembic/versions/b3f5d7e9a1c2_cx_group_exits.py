"""CX / SX: combined exits over a group of the user's own positions (the one trigger engine fires them)

Revision ID: b3f5d7e9a1c2
Revises: d4a6c8e0b2f1 (DATA-H, the index-history backfill)
Create Date: 2026-10-24 19:00:00

- group_exits: one row per combined stop-loss / target over a set of open positions (CX), or the strategy exits set
  with a spread / basket ticket (SX): the legs, the measure (₹ MTM, net premium, spot level), the levels, touch or
  candle close, the trailing state, the status and its version.
One new table only; nothing existing is altered; no backfill.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b3f5d7e9a1c2'
down_revision: Union[str, Sequence[str], None] = 'd4a6c8e0b2f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("one new table only: the previous release never reads or writes group_exits, so it runs unchanged "
                        "on the upgraded schema; the downgrade drops the table and its indexes")

PRICE = sa.Numeric(18, 4, asdecimal=False)
TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'group_exits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('client_ref', sa.String(length=80), nullable=False),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('origin', sa.String(length=4), nullable=False),
        sa.Column('scope', sa.String(length=10), nullable=False),
        sa.Column('basket_ref', sa.String(length=64), nullable=True),
        sa.Column('legs_json', sa.Text(), nullable=False),
        sa.Column('measure', sa.String(length=12), nullable=False),
        sa.Column('spot_symbol', sa.String(length=64), nullable=True),
        sa.Column('spot_exchange', sa.String(length=16), nullable=True),
        sa.Column('sl_value', PRICE, nullable=True),
        sa.Column('sl_side', sa.String(length=6), nullable=True),
        sa.Column('target_value', PRICE, nullable=True),
        sa.Column('target_side', sa.String(length=6), nullable=True),
        sa.Column('basis_value', PRICE, nullable=True),
        sa.Column('check_mode', sa.String(length=6), nullable=False),
        sa.Column('timeframe_minutes', sa.Integer(), nullable=True),
        sa.Column('trail_kind', sa.String(length=12), nullable=False),
        sa.Column('trail_json', sa.Text(), nullable=True),
        sa.Column('best_value', PRICE, nullable=True),
        sa.Column('current_sl', PRICE, nullable=True),
        sa.Column('locked_at', TZ, nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('status_note', sa.Text(), nullable=True),
        sa.Column('fired_kind', sa.String(length=6), nullable=True),
        sa.Column('fired_at', TZ, nullable=True),
        sa.Column('fired_value', PRICE, nullable=True),
        sa.Column('last_value', PRICE, nullable=True),
        sa.Column('last_checked_at', TZ, nullable=True),
        sa.Column('passes_firing', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('trail_log', sa.Text(), nullable=True),
        sa.Column('created_at', TZ, nullable=False),
        sa.Column('updated_at', TZ, nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'user_id', 'client_ref', name='uq_group_exit_client_ref'),
    )
    op.create_index('ix_group_exits_tenant_status', 'group_exits', ['tenant_id', 'status'], unique=False)
    op.create_index(op.f('ix_group_exits_user_id'), 'group_exits', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_group_exits_user_id'), table_name='group_exits')
    op.drop_index('ix_group_exits_tenant_status', table_name='group_exits')
    op.drop_table('group_exits')

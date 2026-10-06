"""P0.4 / S10: money and price columns become NUMERIC (amounts 18,2; traded prices and levels 18,4)

Float columns rounded rupee totals through binary floating point. Postgres casts FLOAT -> NUMERIC implicitly,
rounding to the scale; the application keeps reading floats (`asdecimal=False` on the model types).

Revision ID: c4d6e8f0a2b4
Revises: b3c5d7e9f1a3
Create Date: 2026-10-06 14:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c4d6e8f0a2b4'
down_revision: Union[str, Sequence[str], None] = 'b3c5d7e9f1a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('billing_gateway_plans') as batch_op:
        batch_op.alter_column('amount', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=False)
    with op.batch_alter_table('billing_transactions') as batch_op:
        batch_op.alter_column('amount', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=False)
    with op.batch_alter_table('contract_notes') as batch_op:
        batch_op.alter_column('total_charges', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=False)
    with op.batch_alter_table('marketplace_payouts') as batch_op:
        batch_op.alter_column('amount', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=False)
    with op.batch_alter_table('broker_accounts') as batch_op:
        batch_op.alter_column('available_balance', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=True)
        batch_op.alter_column('used_margin', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=True)
        batch_op.alter_column('realized_pnl', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=True)
        batch_op.alter_column('unrealized_pnl', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=True)
    with op.batch_alter_table('marketplace_listings') as batch_op:
        batch_op.alter_column('price', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=False)
    with op.batch_alter_table('marketplace_charges') as batch_op:
        batch_op.alter_column('amount', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=False)
        batch_op.alter_column('platform_fee', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=False)
        batch_op.alter_column('creator_net', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=False)
    with op.batch_alter_table('trades') as batch_op:
        batch_op.alter_column('entry_price', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=False)
        batch_op.alter_column('stop_loss', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=False)
        batch_op.alter_column('target1', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=True)
        batch_op.alter_column('target2', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=True)
        batch_op.alter_column('exit_price', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=True)
        batch_op.alter_column('pnl', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=True)
        batch_op.alter_column('charges', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=False)
        batch_op.alter_column('underlying_stop_loss', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=True)
        batch_op.alter_column('underlying_target1', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=True)
        batch_op.alter_column('underlying_target2', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=True)
        batch_op.alter_column('expected_price', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=True)
        batch_op.alter_column('slippage', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=True)
        batch_op.alter_column('initial_stop_loss', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=True)
        batch_op.alter_column('best_price', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=True)
    with op.batch_alter_table('contract_note_lines') as batch_op:
        batch_op.alter_column('price', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=4), existing_nullable=False)
        batch_op.alter_column('charges', existing_type=sa.Float(), type_=sa.Numeric(precision=18, scale=2), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('billing_gateway_plans') as batch_op:
        batch_op.alter_column('amount', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=False)
    with op.batch_alter_table('billing_transactions') as batch_op:
        batch_op.alter_column('amount', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=False)
    with op.batch_alter_table('contract_notes') as batch_op:
        batch_op.alter_column('total_charges', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=False)
    with op.batch_alter_table('marketplace_payouts') as batch_op:
        batch_op.alter_column('amount', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=False)
    with op.batch_alter_table('broker_accounts') as batch_op:
        batch_op.alter_column('available_balance', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('used_margin', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('realized_pnl', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('unrealized_pnl', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=True)
    with op.batch_alter_table('marketplace_listings') as batch_op:
        batch_op.alter_column('price', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=False)
    with op.batch_alter_table('marketplace_charges') as batch_op:
        batch_op.alter_column('amount', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=False)
        batch_op.alter_column('platform_fee', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=False)
        batch_op.alter_column('creator_net', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=False)
    with op.batch_alter_table('trades') as batch_op:
        batch_op.alter_column('entry_price', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=False)
        batch_op.alter_column('stop_loss', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=False)
        batch_op.alter_column('target1', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('target2', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('exit_price', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('pnl', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('charges', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=False)
        batch_op.alter_column('underlying_stop_loss', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('underlying_target1', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('underlying_target2', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('expected_price', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('slippage', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('initial_stop_loss', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=True)
        batch_op.alter_column('best_price', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=True)
    with op.batch_alter_table('contract_note_lines') as batch_op:
        batch_op.alter_column('price', existing_type=sa.Numeric(precision=18, scale=4), type_=sa.Float(), existing_nullable=False)
        batch_op.alter_column('charges', existing_type=sa.Numeric(precision=18, scale=2), type_=sa.Float(), existing_nullable=False)

"""widen trade/order quantity to float (fractional crypto sizing)

Revision ID: b7d4f9a3c821
Revises: a1c9d3e7f204
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d4f9a3c821'
down_revision: Union[str, Sequence[str], None] = 'a1c9d3e7f204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A pure widening (Integer -> Float): every existing whole-number quantity round-trips
    # exactly, but a new MCX/crypto fill can now be fractional (see app/instruments/registry.py).
    op.alter_column('trades', 'quantity', existing_type=sa.Integer(), type_=sa.Float(), nullable=False)
    op.alter_column('orders', 'quantity', existing_type=sa.Integer(), type_=sa.Float(), nullable=False)


def downgrade() -> None:
    op.alter_column('orders', 'quantity', existing_type=sa.Float(), type_=sa.Integer(), nullable=False)
    op.alter_column('trades', 'quantity', existing_type=sa.Float(), type_=sa.Integer(), nullable=False)

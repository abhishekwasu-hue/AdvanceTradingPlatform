"""P0.2 / S8: users.mfa_last_step - the last accepted TOTP step, so a code is accepted once

Revision ID: f1a3b5c7d9e1
Revises: e0f2a4b6c8d0
Create Date: 2026-10-06 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f1a3b5c7d9e1'
down_revision: Union[str, Sequence[str], None] = 'e0f2a4b6c8d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('mfa_last_step', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('mfa_last_step')

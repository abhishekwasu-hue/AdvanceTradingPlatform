"""Phase P: tenant base currency and FX rates

Revision ID: a3c5e7f9b1d2
Revises: f2b8d6e4a7c1
Create Date: 2026-09-27 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a3c5e7f9b1d2'
down_revision: Union[str, Sequence[str], None] = 'f2b8d6e4a7c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenants') as batch:
        batch.add_column(sa.Column('base_currency', sa.String(length=4), nullable=False, server_default='INR'))
    op.create_table(
        'fx_rates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('base', sa.String(length=4), nullable=False),
        sa.Column('quote', sa.String(length=4), nullable=False),
        sa.Column('rate', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=40), nullable=False, server_default='manual'),
        sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.UniqueConstraint('base', 'quote', name='uq_fx_rates_pair'),
    )


def downgrade() -> None:
    op.drop_table('fx_rates')
    with op.batch_alter_table('tenants') as batch:
        batch.drop_column('base_currency')

"""Phase X: marketplace prices, charges and creator payouts

Revision ID: b1d3f5a7c9e2
Revises: a9c1e3f5b7d9
Create Date: 2026-09-29 06:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b1d3f5a7c9e2'
down_revision: Union[str, Sequence[str], None] = 'a9c1e3f5b7d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('marketplace_listings') as batch:
        batch.add_column(sa.Column('price', sa.Float(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'))
        batch.add_column(sa.Column('platform_fee_pct', sa.Float(), nullable=True))
    with op.batch_alter_table('marketplace_subscriptions') as batch:
        batch.alter_column('status', type_=sa.String(length=16), existing_type=sa.String(length=12), existing_nullable=False)
    op.create_table(
        'marketplace_payouts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('requested_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='REQUESTED'),
        sa.Column('destination_encrypted', sa.Text(), nullable=False),
        sa.Column('destination_hint', sa.String(length=40), nullable=False),
        sa.Column('reference', sa.String(length=200), nullable=True),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('settled_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_marketplace_payouts_tenant_id', 'marketplace_payouts', ['tenant_id'])
    op.create_index('ix_marketplace_payouts_created_at', 'marketplace_payouts', ['created_at'])
    op.create_table(
        'marketplace_charges',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('listing_id', sa.Integer(), sa.ForeignKey('marketplace_listings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('creator_tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('subscription_id', sa.Integer(), sa.ForeignKey('marketplace_subscriptions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'),
        sa.Column('platform_fee_pct', sa.Float(), nullable=False),
        sa.Column('platform_fee', sa.Float(), nullable=False),
        sa.Column('creator_net', sa.Float(), nullable=False),
        sa.Column('status', sa.String(length=8), nullable=False, server_default='OPEN'),
        sa.Column('provider', sa.String(length=30), nullable=False, server_default='manual'),
        sa.Column('provider_ref', sa.String(length=200), nullable=True),
        sa.Column('checkout_url', sa.String(length=500), nullable=True),
        sa.Column('payment_ref', sa.String(length=200), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('payout_id', sa.Integer(), sa.ForeignKey('marketplace_payouts.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    for col in ('listing_id', 'creator_tenant_id', 'tenant_id', 'payout_id', 'created_at'):
        op.create_index(f'ix_marketplace_charges_{col}', 'marketplace_charges', [col])


def downgrade() -> None:
    for col in ('listing_id', 'creator_tenant_id', 'tenant_id', 'payout_id', 'created_at'):
        op.drop_index(f'ix_marketplace_charges_{col}', table_name='marketplace_charges')
    op.drop_table('marketplace_charges')
    op.drop_index('ix_marketplace_payouts_created_at', table_name='marketplace_payouts')
    op.drop_index('ix_marketplace_payouts_tenant_id', table_name='marketplace_payouts')
    op.drop_table('marketplace_payouts')
    with op.batch_alter_table('marketplace_subscriptions') as batch:
        batch.alter_column('status', type_=sa.String(length=12), existing_type=sa.String(length=16), existing_nullable=False)
    with op.batch_alter_table('marketplace_listings') as batch:
        for col in ('platform_fee_pct', 'currency', 'price'):
            batch.drop_column(col)

"""Phase N: per-tenant data keys, scope overrides, email verification

Revision ID: f2b8d6e4a7c1
Revises: e1a7c5d3f6b0
Create Date: 2026-09-27 09:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f2b8d6e4a7c1'
down_revision: Union[str, Sequence[str], None] = 'e1a7c5d3f6b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tenant_keys',
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('wrapped_key', sa.Text(), nullable=False),
        sa.Column('key_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        'email_verifications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_email_verifications_user_id', 'email_verifications', ['user_id'])
    op.create_unique_constraint('uq_email_verifications_token_hash', 'email_verifications', ['token_hash'])
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('scope_overrides', sa.Text(), nullable=True))
    # Accounts that predate verification are grandfathered: they were created by people who
    # already had broker credentials and LIVE deployments; locking them out would be a regression.
    op.execute("UPDATE users SET email_verified_at = created_at WHERE email_verified_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('scope_overrides')
        batch.drop_column('email_verified_at')
    op.drop_constraint('uq_email_verifications_token_hash', 'email_verifications', type_='unique')
    op.drop_index('ix_email_verifications_user_id', table_name='email_verifications')
    op.drop_table('email_verifications')
    op.drop_table('tenant_keys')

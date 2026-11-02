"""M73 SL-2: `user_identities` - the sign-in providers linked to an account (Google, Facebook; later a broker, M74).
One row = one provider account (`provider` + `subject` unique: a provider account belongs to one ATP account) and one
provider per user. `email_at_link` is the provider's e-mail when it was linked (nullable: a provider may give none).
No provider token is ever stored. Personal data: cascades with the user, removed on erasure.

Revision ID: c4e8a2f6b913
Revises: 6b9d1f3a5c27
Create Date: 2026-11-01 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c4e8a2f6b913'
down_revision: str | Sequence[str] | None = '6b9d1f3a5c27'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLLBACK_SAFE = True     # Z11: additive (one new table) - the previous release runs on this schema (code-only rollback ok)


def upgrade() -> None:
    op.create_table(
        'user_identities',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', sa.String(40), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('email_at_link', sa.String(320), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('provider', 'subject', name='uq_user_identities_provider_subject'),
        sa.UniqueConstraint('user_id', 'provider', name='uq_user_identities_user_provider'),
    )
    op.create_index('ix_user_identities_user_id', 'user_identities', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_user_identities_user_id', table_name='user_identities')
    op.drop_table('user_identities')

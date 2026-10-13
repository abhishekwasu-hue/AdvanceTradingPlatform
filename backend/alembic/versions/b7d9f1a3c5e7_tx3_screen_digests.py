"""U9 TX3: screen_digests - one user's setup-of-the-day digest (pinned screens, symbols, time, last digest)

Revision ID: b7d9f1a3c5e7
Revises: c9e1f3a5b7d2
Create Date: 2026-10-12 22:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b7d9f1a3c5e7'
down_revision: Union[str, Sequence[str], None] = 'c9e1f3a5b7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - a new table only; the previous release runs on this schema (code-only rollback ok)


def upgrade() -> None:
    op.create_table(
        'screen_digests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('send_time', sa.String(length=5), nullable=False),
        sa.Column('pinned_json', sa.Text(), nullable=False),
        sa.Column('symbols_json', sa.Text(), nullable=False),
        sa.Column('exchange', sa.String(length=8), nullable=False),
        sa.Column('last_sent_on', sa.Date(), nullable=True),
        sa.Column('last_digest_json', sa.Text(), nullable=True),
        sa.Column('last_status', sa.String(length=300), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index('ix_screen_digests_tenant_id', 'screen_digests', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('ix_screen_digests_tenant_id', table_name='screen_digests')
    op.drop_table('screen_digests')

"""Part D6: evidence for the go-live checklist items (vendor, strategy class, AI, DPDP)

Revision ID: e6a1b2c3d4f5
Revises: b4d6f8a0c2e4
Create Date: 2026-10-10 23:15:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e6a1b2c3d4f5'
down_revision: Union[str, Sequence[str], None] = 'b4d6f8a0c2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'compliance_evidence',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('item_id', sa.String(length=60), nullable=False),
        sa.Column('reference', sa.String(length=500), nullable=False),
        sa.Column('valid_until', sa.Date(), nullable=True),
        sa.Column('recorded_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_compliance_evidence_item_id', 'compliance_evidence', ['item_id'])


def downgrade() -> None:
    op.drop_index('ix_compliance_evidence_item_id', table_name='compliance_evidence')
    op.drop_table('compliance_evidence')

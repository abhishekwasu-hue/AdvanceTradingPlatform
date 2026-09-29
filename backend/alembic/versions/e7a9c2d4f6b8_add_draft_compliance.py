"""Phase V2: compliance report on AI strategy drafts

Revision ID: e7a9c2d4f6b8
Revises: d6f8b1c3e5a7
Create Date: 2026-09-28 19:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e7a9c2d4f6b8'
down_revision: Union[str, Sequence[str], None] = 'd6f8b1c3e5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('ai_strategy_drafts') as batch:
        batch.add_column(sa.Column('compliance_json', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('ai_strategy_drafts') as batch:
        batch.drop_column('compliance_json')

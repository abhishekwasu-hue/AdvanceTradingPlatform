"""Phase V3: prompt version, runtime context and deployment suggestion on AI drafts

Revision ID: f8b1d3e5a7c9
Revises: e7a9c2d4f6b8
Create Date: 2026-09-29 06:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f8b1d3e5a7c9'
down_revision: Union[str, Sequence[str], None] = 'e7a9c2d4f6b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('ai_strategy_drafts') as batch:
        batch.add_column(sa.Column('prompt_version', sa.String(length=40), nullable=True))
        batch.add_column(sa.Column('context_json', sa.Text(), nullable=True))
        batch.add_column(sa.Column('deployment_json', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('ai_strategy_drafts') as batch:
        for col in ('deployment_json', 'context_json', 'prompt_version'):
            batch.drop_column(col)

"""PW4: the Telegram message of a proposal (chat id + message id), so app decisions and expiry update it.

Revision ID: e9c1a3b5d7f9
Revises: d4e6f8a0b2c9
Create Date: 2026-10-11 10:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e9c1a3b5d7f9'
down_revision: Union[str, Sequence[str], None] = 'd4e6f8a0b2c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ai_actions', sa.Column('telegram_chat_id', sa.String(64), nullable=True))
    op.add_column('ai_actions', sa.Column('telegram_message_id', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('ai_actions', 'telegram_message_id')
    op.drop_column('ai_actions', 'telegram_chat_id')

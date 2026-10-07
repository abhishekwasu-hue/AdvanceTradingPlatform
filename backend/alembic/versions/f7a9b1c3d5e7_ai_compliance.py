"""P0.8-D: ai_acknowledgements (Copilot first-use terms, data-sharing consent) and llm_calls (every LLM input/output)

Revision ID: f7a9b1c3d5e7
Revises: e6f8a0b2c4d6
Create Date: 2026-10-07 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f7a9b1c3d5e7'
down_revision: Union[str, Sequence[str], None] = 'e6f8a0b2c4d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_acknowledgements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('version', sa.String(length=24), nullable=False),
        sa.Column('text_sha256', sa.String(length=64), nullable=False),
        sa.Column('language', sa.String(length=4), nullable=False),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=300), nullable=True),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ai_acknowledgements_tenant_id', 'ai_acknowledgements', ['tenant_id'])
    op.create_index('ix_ai_acknowledgements_user_id', 'ai_acknowledgements', ['user_id'])
    op.create_index('ix_ai_acknowledgements_kind', 'ai_acknowledgements', ['kind'])
    op.create_table(
        'llm_calls',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('feature', sa.String(length=40), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('model', sa.String(length=80), nullable=False),
        sa.Column('prompt_version', sa.String(length=40), nullable=True),
        sa.Column('system_sha256', sa.String(length=64), nullable=False),
        sa.Column('user_sha256', sa.String(length=64), nullable=False),
        sa.Column('response_sha256', sa.String(length=64), nullable=True),
        sa.Column('system_text', sa.Text(), nullable=False),
        sa.Column('user_text', sa.Text(), nullable=False),
        sa.Column('response_text', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=300), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cost_usd', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_llm_calls_tenant_id', 'llm_calls', ['tenant_id'])
    op.create_index('ix_llm_calls_user_id', 'llm_calls', ['user_id'])
    op.create_index('ix_llm_calls_feature', 'llm_calls', ['feature'])
    op.create_index('ix_llm_calls_created_at', 'llm_calls', ['created_at'])


def downgrade() -> None:
    for name in ('ix_llm_calls_created_at', 'ix_llm_calls_feature', 'ix_llm_calls_user_id', 'ix_llm_calls_tenant_id'):
        op.drop_index(name, table_name='llm_calls')
    op.drop_table('llm_calls')
    for name in ('ix_ai_acknowledgements_kind', 'ix_ai_acknowledgements_user_id', 'ix_ai_acknowledgements_tenant_id'):
        op.drop_index(name, table_name='ai_acknowledgements')
    op.drop_table('ai_acknowledgements')

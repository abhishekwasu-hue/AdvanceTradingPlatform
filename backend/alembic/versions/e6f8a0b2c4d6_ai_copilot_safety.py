"""P0.8-A: ai_candidates (server-held strategist/interview candidates), one open proposal per rule, custom: ids

Revision ID: e6f8a0b2c4d6
Revises: d5e7f9a1b3c5
Create Date: 2026-10-06 17:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e6f8a0b2c4d6'
down_revision: Union[str, Sequence[str], None] = 'd5e7f9a1b3c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OPEN = "status IN ('PROPOSED', 'APPROVED')"


def upgrade() -> None:
    op.create_table(
        'ai_candidates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('strategy_id', sa.String(length=100), nullable=True),
        sa.Column('config_json', sa.Text(), nullable=True),
        sa.Column('metrics_json', sa.Text(), nullable=False),
        sa.Column('deployment_json', sa.Text(), nullable=True),
        sa.Column('risk_json', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('adopted_strategy_id', sa.Integer(), nullable=True),
        sa.Column('deployment_id', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['adopted_strategy_id'], ['custom_strategies.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['deployment_id'], ['strategy_deployments.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ai_candidates_tenant_id', 'ai_candidates', ['tenant_id'])
    op.create_index('ix_ai_candidates_status', 'ai_candidates', ['status'])
    op.create_index('ix_ai_candidates_created_at', 'ai_candidates', ['created_at'])
    # A5: the database refuses a second open proposal for the same tenant/deployment/rule.
    op.create_index('uq_ai_actions_open_rule', 'ai_actions', ['tenant_id', 'deployment_id', 'rule'], unique=True,
                    postgresql_where=sa.text(_OPEN), sqlite_where=sa.text(_OPEN))
    # A4: deployments created from an AI draft / marketplace copy carried the `custom_<id>` spelling the resolver never
    # recognised; the resolver now accepts both, and stored rows are normalised to `custom:<id>`.
    op.execute(sa.text("UPDATE strategy_deployments SET strategy_id = 'custom:' || substr(strategy_id, 8) WHERE strategy_id LIKE 'custom\\_%' ESCAPE '\\'"))


def downgrade() -> None:
    op.drop_index('uq_ai_actions_open_rule', table_name='ai_actions')
    op.drop_index('ix_ai_candidates_created_at', table_name='ai_candidates')
    op.drop_index('ix_ai_candidates_status', table_name='ai_candidates')
    op.drop_index('ix_ai_candidates_tenant_id', table_name='ai_candidates')
    op.drop_table('ai_candidates')

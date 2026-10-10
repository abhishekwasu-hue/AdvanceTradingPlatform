"""H-C2: agent_runs and agent_steps (the Copilot agent's audit trail)

Revision ID: f1a2b3c4d5e6
Revises: a1c3e5f7b9d2
Create Date: 2026-10-11 02:10:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'a1c3e5f7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('question_sha256', sa.String(length=64), nullable=False),
        sa.Column('prompt_version', sa.String(length=80), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('limits_json', sa.Text(), nullable=False),
        sa.Column('outcome', sa.String(length=20), nullable=False),
        sa.Column('steps', sa.Integer(), nullable=False),
        sa.Column('tool_calls', sa.Integer(), nullable=False),
        sa.Column('error', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_agent_runs_tenant_id', 'agent_runs', ['tenant_id'])
    op.create_index('ix_agent_runs_user_id', 'agent_runs', ['user_id'])
    op.create_table(
        'agent_steps',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('agent_runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('step', sa.Integer(), nullable=False),
        sa.Column('tool_name', sa.String(length=60), nullable=False),
        sa.Column('arguments_json', sa.Text(), nullable=False),
        sa.Column('ok', sa.Boolean(), nullable=False),
        sa.Column('output_sha256', sa.String(length=64), nullable=False),
        sa.Column('untrusted', sa.Boolean(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('error', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_agent_steps_run_id', 'agent_steps', ['run_id'])


def downgrade() -> None:
    op.drop_index('ix_agent_steps_run_id', table_name='agent_steps')
    op.drop_table('agent_steps')
    op.drop_index('ix_agent_runs_user_id', table_name='agent_runs')
    op.drop_index('ix_agent_runs_tenant_id', table_name='agent_runs')
    op.drop_table('agent_runs')

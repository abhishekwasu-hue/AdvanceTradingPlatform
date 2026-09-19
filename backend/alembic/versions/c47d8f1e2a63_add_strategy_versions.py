"""add strategy versions (immutable custom-strategy history)

Revision ID: c47d8f1e2a63
Revises: b2e7f4a9c815
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c47d8f1e2a63'
down_revision: Union[str, Sequence[str], None] = 'b2e7f4a9c815'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Creates strategy_versions and backfills a version 1 (source='created', status='LIVE')
    for every existing custom_strategies row from its current config_json, then points
    live_version_id at it - so pre-existing strategies keep working unchanged and immediately
    have a real (if single-entry) version history.
    """
    op.create_table(
        'strategy_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('custom_strategy_id', sa.Integer(), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('config_json', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['custom_strategy_id'], ['custom_strategies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('custom_strategy_id', 'version_number', name='uq_strategy_version'),
    )
    op.create_index(op.f('ix_strategy_versions_tenant_id'), 'strategy_versions', ['tenant_id'])
    op.create_index(op.f('ix_strategy_versions_custom_strategy_id'), 'strategy_versions', ['custom_strategy_id'])

    op.execute(
        """
        INSERT INTO strategy_versions (tenant_id, custom_strategy_id, version_number, config_json, source, status, created_by, created_at)
        SELECT tenant_id, id, 1, config_json, 'created', 'LIVE', user_id, created_at FROM custom_strategies
        """
    )

    op.add_column('custom_strategies', sa.Column('live_version_id', sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE custom_strategies SET live_version_id = strategy_versions.id
        FROM strategy_versions
        WHERE strategy_versions.custom_strategy_id = custom_strategies.id AND strategy_versions.version_number = 1
        """
    )
    op.create_foreign_key(
        'fk_custom_strategies_live_version_id', 'custom_strategies', 'strategy_versions',
        ['live_version_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_custom_strategies_live_version_id', 'custom_strategies', type_='foreignkey')
    op.drop_column('custom_strategies', 'live_version_id')

    op.drop_index(op.f('ix_strategy_versions_custom_strategy_id'), table_name='strategy_versions')
    op.drop_index(op.f('ix_strategy_versions_tenant_id'), table_name='strategy_versions')
    op.drop_table('strategy_versions')

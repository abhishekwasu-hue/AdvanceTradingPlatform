"""TM 9.4: the tenant-private chain history (md_tenant_chain_snapshots) and TM-7's owner on the OI collector rows

Revision ID: 250d796123b7
Revises: e8a0c2d4f6b9
Create Date: 2026-10-23 19:00:00

- md_tenant_chain_snapshots: new table (one recorded option chain per tenant, underlying, expiry and slot; the strikes
  as zlib-compressed JSON).
- oi_snapshots / oi_day_baselines: a nullable tenant_id (the organisation whose own broker session read the slot; NULL
  = stored before this release), and the per-slot / per-strike uniqueness now includes it, so each organisation keeps
  its own slots (TM-7 / RDY-6: never another user's feed).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '250d796123b7'
down_revision: Union[str, Sequence[str], None] = 'e8a0c2d4f6b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("the foreign keys and the new unique constraints are on the new nullable tenant_id columns: the "
                        "previous release writes NULL there (accepted by the key; NULLs never collide in a unique "
                        "constraint) and keeps its own check-before-insert per (underlying, slot); the dropped constraints "
                        "only allowed fewer rows; md_tenant_chain_snapshots is a new table no earlier release touches")


def upgrade() -> None:
    op.create_table(
        'md_tenant_chain_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('underlying', sa.String(length=30), nullable=False),
        sa.Column('expiry', sa.Date(), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('slot_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('spot', sa.Float(), nullable=True),
        sa.Column('strikes', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=40), nullable=False),
        sa.Column('codec', sa.String(length=20), nullable=False),
        sa.Column('raw_bytes', sa.Integer(), nullable=False),
        sa.Column('payload', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'underlying', 'expiry', 'slot_start', name='uq_md_tenant_chain_slot'),
    )
    op.create_index('ix_md_tenant_chain_day', 'md_tenant_chain_snapshots', ['tenant_id', 'underlying', 'trade_date'], unique=False)

    op.add_column('oi_snapshots', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_oi_snapshots_tenant_id', 'oi_snapshots', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint('uq_oi_snapshot_slot', 'oi_snapshots', type_='unique')
    op.create_unique_constraint('uq_oi_snapshot_tenant_slot', 'oi_snapshots', ['tenant_id', 'underlying', 'slot_start'])
    op.create_index('ix_oi_snapshots_tenant_day', 'oi_snapshots', ['tenant_id', 'underlying', 'trade_date'], unique=False)

    op.add_column('oi_day_baselines', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_oi_day_baselines_tenant_id', 'oi_day_baselines', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint('uq_oi_day_baseline', 'oi_day_baselines', type_='unique')
    op.create_unique_constraint('uq_oi_day_baseline_tenant', 'oi_day_baselines', ['tenant_id', 'underlying', 'trade_date', 'strike'])
    op.create_index(op.f('ix_oi_day_baselines_tenant_id'), 'oi_day_baselines', ['tenant_id'], unique=False)


def downgrade() -> None:
    # The older schema keeps one slot per (underlying, slot) and one baseline per (underlying, day, strike): the lowest-id
    # row of each is kept (its strikes go with a removed slot through the cascade), then the tenant columns are dropped.
    op.drop_index(op.f('ix_oi_day_baselines_tenant_id'), table_name='oi_day_baselines')
    op.drop_constraint('uq_oi_day_baseline_tenant', 'oi_day_baselines', type_='unique')
    op.execute("DELETE FROM oi_day_baselines WHERE id NOT IN (SELECT MIN(id) FROM oi_day_baselines GROUP BY underlying, trade_date, strike)")
    op.create_unique_constraint('uq_oi_day_baseline', 'oi_day_baselines', ['underlying', 'trade_date', 'strike'])
    op.drop_constraint('fk_oi_day_baselines_tenant_id', 'oi_day_baselines', type_='foreignkey')
    op.drop_column('oi_day_baselines', 'tenant_id')

    op.drop_index('ix_oi_snapshots_tenant_day', table_name='oi_snapshots')
    op.drop_constraint('uq_oi_snapshot_tenant_slot', 'oi_snapshots', type_='unique')
    op.execute("DELETE FROM oi_snapshots WHERE id NOT IN (SELECT MIN(id) FROM oi_snapshots GROUP BY underlying, slot_start)")
    op.create_unique_constraint('uq_oi_snapshot_slot', 'oi_snapshots', ['underlying', 'slot_start'])
    op.drop_constraint('fk_oi_snapshots_tenant_id', 'oi_snapshots', type_='foreignkey')
    op.drop_column('oi_snapshots', 'tenant_id')

    op.drop_index('ix_md_tenant_chain_day', table_name='md_tenant_chain_snapshots')
    op.drop_table('md_tenant_chain_snapshots')

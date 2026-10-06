"""P0.3 / S7: audit_logs foreign keys RESTRICT (SET NULL would rewrite hashed fields), audit_anchors table

Revision ID: a2b4c6d8e0f2
Revises: f1a3b5c7d9e1
Create Date: 2026-10-06 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a2b4c6d8e0f2'
down_revision: Union[str, Sequence[str], None] = 'f1a3b5c7d9e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fk_names(table: str) -> dict:
    """Constraint names as the database has them (Postgres: <table>_<col>_fkey; batch mode on SQLite recreates)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {fk['constrained_columns'][0]: fk['name'] for fk in inspector.get_foreign_keys(table) if fk.get('name')}


def upgrade() -> None:
    op.create_table(
        'audit_anchors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('last_id', sa.Integer(), nullable=False),
        sa.Column('head_hash', sa.String(length=64), nullable=False),
        sa.Column('anchored_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    names = _fk_names('audit_logs')
    with op.batch_alter_table('audit_logs') as batch_op:
        for column, ref in (('tenant_id', 'tenants'), ('user_id', 'users')):
            if column in names:
                batch_op.drop_constraint(names[column], type_='foreignkey')
            batch_op.create_foreign_key(f'fk_audit_logs_{column}_{ref}', ref, [column], ['id'], ondelete='RESTRICT')


def downgrade() -> None:
    names = _fk_names('audit_logs')
    with op.batch_alter_table('audit_logs') as batch_op:
        for column, ref in (('tenant_id', 'tenants'), ('user_id', 'users')):
            if column in names:
                batch_op.drop_constraint(names[column], type_='foreignkey')
            batch_op.create_foreign_key(f'audit_logs_{column}_fkey', ref, [column], ['id'], ondelete='SET NULL')
    op.drop_table('audit_anchors')

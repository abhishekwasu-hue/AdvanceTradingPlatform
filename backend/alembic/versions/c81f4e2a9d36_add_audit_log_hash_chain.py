"""add tamper-evident hash chain to audit_logs (master prompt Section 48)

Revision ID: c81f4e2a9d36
Revises: b7d4f9a3c821
Create Date: 2026-09-19 00:00:00.000000

"""
import hashlib
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c81f4e2a9d36'
down_revision: Union[str, Sequence[str], None] = 'b7d4f9a3c821'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GENESIS_HASH = "GENESIS"


def _compute_hash(prev_hash, tenant_id, user_id, event, detail, created_at) -> str:
    # Must match app/audit/log.py::_compute_hash/_normalize_timestamp exactly - duplicated here
    # (rather than imported) since a migration must keep working unchanged even after that
    # function's implementation someday changes. The tzinfo-stripping step matters: without it,
    # a backfilled row's hash could permanently disagree with one computed by the app for the
    # exact same timestamp, purely over a "+00:00" suffix (see _normalize_timestamp's docstring).
    if created_at.tzinfo is not None:
        from datetime import timezone
        created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
    payload = "|".join([prev_hash, str(tenant_id), str(user_id), event, detail, created_at.isoformat()])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column('audit_logs', sa.Column('prev_hash', sa.String(length=64), nullable=False, server_default=''))
    op.add_column('audit_logs', sa.Column('hash', sa.String(length=64), nullable=False, server_default=''))

    # Backfill: any audit_logs rows written before this migration existed had no hash chain -
    # compute one now, in id order, so the chain covers the table's entire history rather than
    # only rows written after upgrade.
    bind = op.get_bind()
    audit_logs = sa.table(
        'audit_logs',
        sa.column('id', sa.Integer()), sa.column('tenant_id', sa.Integer()), sa.column('user_id', sa.Integer()),
        sa.column('event', sa.String()), sa.column('detail', sa.Text()), sa.column('created_at', sa.DateTime()),
        sa.column('prev_hash', sa.String()), sa.column('hash', sa.String()),
    )
    rows = bind.execute(sa.select(audit_logs.c.id, audit_logs.c.tenant_id, audit_logs.c.user_id,
                                   audit_logs.c.event, audit_logs.c.detail, audit_logs.c.created_at)
                         .order_by(audit_logs.c.id.asc())).fetchall()

    prev_hash = GENESIS_HASH
    for row in rows:
        row_hash = _compute_hash(prev_hash, row.tenant_id, row.user_id, row.event, row.detail, row.created_at)
        bind.execute(
            audit_logs.update().where(audit_logs.c.id == row.id).values(prev_hash=prev_hash, hash=row_hash)
        )
        prev_hash = row_hash

    op.alter_column('audit_logs', 'prev_hash', server_default=None)
    op.alter_column('audit_logs', 'hash', server_default=None)


def downgrade() -> None:
    op.drop_column('audit_logs', 'hash')
    op.drop_column('audit_logs', 'prev_hash')

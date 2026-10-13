"""N7 I1: `option_chain_snapshots.ingested_at` - when the platform stored the quote, so options analytics as of a time
read only the quotes known by then (a later upload of older captures does not change an earlier answer).

Existing rows: the recorder's are left NULL - `chain_recorder.known_by` reads a NULL recorder row as known from its
capture (it was written live), so the bulk of the table is not rewritten. Uploads (a small share) get the migration time
(their real upload time was not kept - the conservative choice: they count only from now on for any time asked).

Lock time: ADD COLUMN without a default is a catalogue change (instant). The one UPDATE scans the table (no index on
`source`) and row-locks the uploaded rows only; the recorder's inserts are not blocked. At the retention ceiling (~1-2 M
rows) expect a few seconds on the deploy transaction.

Revision ID: d7e9f1a3b5c7
Revises: c6e1f3a5b7d9
Create Date: 2026-10-13 14:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd7e9f1a3b5c7'
down_revision: Union[str, Sequence[str], None] = 'c6e1f3a5b7d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = True     # Z11: additive - one nullable column the previous release neither reads nor writes (code-only rollback ok)
ROLLBACK_SAFE_REASON = "the UPDATE only fills the new column; no existing column or row the previous release reads is changed"


def upgrade() -> None:
    op.add_column('option_chain_snapshots', sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE option_chain_snapshots SET ingested_at = CURRENT_TIMESTAMP WHERE source LIKE 'upload%'")


def downgrade() -> None:
    op.drop_column('option_chain_snapshots', 'ingested_at')

"""add contract notes (Phase D4): contract_notes, contract_note_lines, trades.exit_order_id /
charges_source / contract_note_id

Revision ID: f8c4d6e2b510
Revises: e7b3c5d1a409
Create Date: 2026-09-26 13:20:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f8c4d6e2b510'
down_revision: Union[str, Sequence[str], None] = 'e7b3c5d1a409'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'contract_notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('uploaded_by', sa.Integer(), nullable=True),
        sa.Column('broker_name', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('filename', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('note_date', sa.Date(), nullable=True),
        sa.Column('line_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('matched_lines', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('trades_updated', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_charges', sa.Float(), nullable=False, server_default='0'),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_contract_notes_tenant_id'), 'contract_notes', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_contract_notes_sha256'), 'contract_notes', ['sha256'], unique=False)

    op.create_table(
        'contract_note_lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contract_note_id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('trade_id', sa.Integer(), nullable=True),
        sa.Column('trade_date', sa.Date(), nullable=True),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('side', sa.String(length=4), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('order_id', sa.String(length=100), nullable=True),
        sa.Column('charges', sa.Float(), nullable=False, server_default='0'),
        sa.Column('breakdown_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('match_method', sa.String(length=20), nullable=False, server_default='UNMATCHED'),
        sa.ForeignKeyConstraint(['contract_note_id'], ['contract_notes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_contract_note_lines_contract_note_id'), 'contract_note_lines', ['contract_note_id'], unique=False)
    op.create_index(op.f('ix_contract_note_lines_tenant_id'), 'contract_note_lines', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_contract_note_lines_trade_id'), 'contract_note_lines', ['trade_id'], unique=False)
    op.create_index(op.f('ix_contract_note_lines_order_id'), 'contract_note_lines', ['order_id'], unique=False)

    op.add_column('trades', sa.Column('exit_order_id', sa.String(length=100), nullable=True))
    op.add_column('trades', sa.Column('charges_source', sa.String(length=20), nullable=False, server_default='ESTIMATED'))
    op.add_column('trades', sa.Column('contract_note_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_trades_contract_note_id', 'trades', 'contract_notes', ['contract_note_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_trades_contract_note_id', 'trades', type_='foreignkey')
    op.drop_column('trades', 'contract_note_id')
    op.drop_column('trades', 'charges_source')
    op.drop_column('trades', 'exit_order_id')
    op.drop_index(op.f('ix_contract_note_lines_order_id'), table_name='contract_note_lines')
    op.drop_index(op.f('ix_contract_note_lines_trade_id'), table_name='contract_note_lines')
    op.drop_index(op.f('ix_contract_note_lines_tenant_id'), table_name='contract_note_lines')
    op.drop_index(op.f('ix_contract_note_lines_contract_note_id'), table_name='contract_note_lines')
    op.drop_table('contract_note_lines')
    op.drop_index(op.f('ix_contract_notes_sha256'), table_name='contract_notes')
    op.drop_index(op.f('ix_contract_notes_tenant_id'), table_name='contract_notes')
    op.drop_table('contract_notes')

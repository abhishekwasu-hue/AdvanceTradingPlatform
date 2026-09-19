"""add multi-tenancy (tenants, tenant_id, roles)

Revision ID: 6f1a2d9b7c31
Revises: 7960f50fb635
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6f1a2d9b7c31'
down_revision: Union[str, Sequence[str], None] = '7960f50fb635'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Introduces the Tenant isolation boundary (spec section 5-6): a tenants table, a
    tenant_id FK on every tenant-owned table, and a role column on users. Every pre-existing
    user gets backfilled into its own brand-new tenant (one-user-per-tenant, matching what
    registration already does going forward), and every tenant-owned row inherits its owning
    user's tenant_id, so behavior for existing single-user data is unchanged after this runs.
    """
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('plan', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # --- users: role + tenant_id (one new tenant per existing user) ---
    op.add_column('users', sa.Column('role', sa.String(length=20), nullable=False, server_default='USER'))
    op.alter_column('users', 'role', server_default=None)
    op.add_column('users', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.execute(
        """
        INSERT INTO tenants (name, plan, status, created_at)
        SELECT email, 'free', 'active', created_at FROM users ORDER BY id
        """
    )
    op.execute(
        """
        UPDATE users SET tenant_id = tenants.id
        FROM tenants WHERE tenants.name = users.email
        """
    )
    op.alter_column('users', 'tenant_id', nullable=False)
    op.create_index(op.f('ix_users_tenant_id'), 'users', ['tenant_id'])
    op.create_foreign_key('fk_users_tenant_id', 'users', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')

    # --- broker_credentials: tenant_id (from owning user), tenant-scoped uniqueness ---
    op.add_column('broker_credentials', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE broker_credentials SET tenant_id = users.tenant_id
        FROM users WHERE users.id = broker_credentials.user_id
        """
    )
    op.alter_column('broker_credentials', 'tenant_id', nullable=False)
    op.create_index(op.f('ix_broker_credentials_tenant_id'), 'broker_credentials', ['tenant_id'])
    op.create_foreign_key(
        'fk_broker_credentials_tenant_id', 'broker_credentials', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE'
    )
    op.drop_constraint('uq_user_broker', 'broker_credentials', type_='unique')
    op.create_unique_constraint('uq_tenant_broker', 'broker_credentials', ['tenant_id', 'broker_name'])

    # --- trades ---
    op.add_column('trades', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.execute("UPDATE trades SET tenant_id = users.tenant_id FROM users WHERE users.id = trades.user_id")
    op.alter_column('trades', 'tenant_id', nullable=False)
    op.create_index(op.f('ix_trades_tenant_id'), 'trades', ['tenant_id'])
    op.create_foreign_key('fk_trades_tenant_id', 'trades', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')

    # --- signal_history ---
    op.add_column('signal_history', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.execute(
        "UPDATE signal_history SET tenant_id = users.tenant_id FROM users WHERE users.id = signal_history.user_id"
    )
    op.alter_column('signal_history', 'tenant_id', nullable=False)
    op.create_index(op.f('ix_signal_history_tenant_id'), 'signal_history', ['tenant_id'])
    op.create_foreign_key(
        'fk_signal_history_tenant_id', 'signal_history', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE'
    )

    # --- custom_strategies ---
    op.add_column('custom_strategies', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.execute(
        "UPDATE custom_strategies SET tenant_id = users.tenant_id FROM users WHERE users.id = custom_strategies.user_id"
    )
    op.alter_column('custom_strategies', 'tenant_id', nullable=False)
    op.create_index(op.f('ix_custom_strategies_tenant_id'), 'custom_strategies', ['tenant_id'])
    op.create_foreign_key(
        'fk_custom_strategies_tenant_id', 'custom_strategies', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE'
    )

    # --- risk_settings: was one row per user, becomes one row per tenant ---
    op.add_column('risk_settings', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.execute(
        "UPDATE risk_settings SET tenant_id = users.tenant_id FROM users WHERE users.id = risk_settings.user_id"
    )
    op.alter_column('risk_settings', 'tenant_id', nullable=False)
    op.drop_index(op.f('ix_risk_settings_user_id'), table_name='risk_settings')
    op.alter_column('risk_settings', 'user_id', new_column_name='updated_by', nullable=True)
    op.drop_constraint('risk_settings_user_id_fkey', 'risk_settings', type_='foreignkey')
    op.create_foreign_key(
        'fk_risk_settings_updated_by', 'risk_settings', 'users', ['updated_by'], ['id'], ondelete='SET NULL'
    )
    op.create_index(op.f('ix_risk_settings_tenant_id'), 'risk_settings', ['tenant_id'], unique=True)
    op.create_foreign_key(
        'fk_risk_settings_tenant_id', 'risk_settings', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE'
    )

    # --- audit_logs: best-effort tenant_id, nullable (rows with no user_id stay untagged) ---
    op.add_column('audit_logs', sa.Column('tenant_id', sa.Integer(), nullable=True))
    op.execute("UPDATE audit_logs SET tenant_id = users.tenant_id FROM users WHERE users.id = audit_logs.user_id")
    op.create_index(op.f('ix_audit_logs_tenant_id'), 'audit_logs', ['tenant_id'])
    op.create_foreign_key(
        'fk_audit_logs_tenant_id', 'audit_logs', 'tenants', ['tenant_id'], ['id'], ondelete='SET NULL'
    )


def downgrade() -> None:
    """Reverses the schema shape (drops tenant_id everywhere, restores risk_settings.user_id and
    broker_credentials' per-user uniqueness). Lossy: once multiple users share a tenant there is
    no way back to "one row per user" for risk_settings, so downgrade only makes sense before any
    tenant has more than its original single user.
    """
    op.drop_constraint('fk_audit_logs_tenant_id', 'audit_logs', type_='foreignkey')
    op.drop_index(op.f('ix_audit_logs_tenant_id'), table_name='audit_logs')
    op.drop_column('audit_logs', 'tenant_id')

    op.drop_constraint('fk_risk_settings_tenant_id', 'risk_settings', type_='foreignkey')
    op.drop_index(op.f('ix_risk_settings_tenant_id'), table_name='risk_settings')
    op.drop_constraint('fk_risk_settings_updated_by', 'risk_settings', type_='foreignkey')
    op.alter_column('risk_settings', 'updated_by', new_column_name='user_id', nullable=False)
    op.create_foreign_key('risk_settings_user_id_fkey', 'risk_settings', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    op.create_index(op.f('ix_risk_settings_user_id'), 'risk_settings', ['user_id'], unique=True)
    op.drop_column('risk_settings', 'tenant_id')

    op.drop_constraint('fk_custom_strategies_tenant_id', 'custom_strategies', type_='foreignkey')
    op.drop_index(op.f('ix_custom_strategies_tenant_id'), table_name='custom_strategies')
    op.drop_column('custom_strategies', 'tenant_id')

    op.drop_constraint('fk_signal_history_tenant_id', 'signal_history', type_='foreignkey')
    op.drop_index(op.f('ix_signal_history_tenant_id'), table_name='signal_history')
    op.drop_column('signal_history', 'tenant_id')

    op.drop_constraint('fk_trades_tenant_id', 'trades', type_='foreignkey')
    op.drop_index(op.f('ix_trades_tenant_id'), table_name='trades')
    op.drop_column('trades', 'tenant_id')

    op.drop_constraint('uq_tenant_broker', 'broker_credentials', type_='unique')
    op.create_unique_constraint('uq_user_broker', 'broker_credentials', ['user_id', 'broker_name'])
    op.drop_constraint('fk_broker_credentials_tenant_id', 'broker_credentials', type_='foreignkey')
    op.drop_index(op.f('ix_broker_credentials_tenant_id'), table_name='broker_credentials')
    op.drop_column('broker_credentials', 'tenant_id')

    op.drop_constraint('fk_users_tenant_id', 'users', type_='foreignkey')
    op.drop_index(op.f('ix_users_tenant_id'), table_name='users')
    op.drop_column('users', 'tenant_id')
    op.drop_column('users', 'role')

    op.drop_table('tenants')

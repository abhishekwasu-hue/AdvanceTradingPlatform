"""DEF-0 D1 / D10: every risk limit is the organisation's own setting - NULL = not set (off)

The risk_settings limit columns become nullable with no server default. A stored value equal to the old platform value
(the owner's personal numbers that came in as defaults: capital 100000, risk per trade 0.5%, daily loss 3%, 20 trades a
day, 3 open positions, 4 losses in a row, portfolio risk 6%, cool-down 30 min, drawdown 5% / 10%, event cut 50%) is set
to NULL: a row cannot tell a value the organisation chose from the one it was handed, and the owner's default for that
is "not set" (DEF-0 decision 2). Every row is copied to risk_settings_def0_backup first; the count per field is printed.
trade_plans.risk_cap becomes nullable (a ticket with no cap set stores none).

Run after 15:30 IST (owner, DECISIONS 25 Oct).

Revision ID: d0f0a1b2c3d4
Revises: f1b3d5e7a9c2
Create Date: 2026-10-28 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0f0a1b2c3d4'
down_revision: Union[str, Sequence[str], None] = 'f1b3d5e7a9c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
ROLLBACK_SAFE = False    # Z11: rewrites rows to NULL and drops server defaults - the previous release reads NULL limits as errors; use --restore-db

_FLOAT_COLUMNS = ('capital', 'risk_per_trade_pct', 'max_daily_loss_pct', 'max_portfolio_risk_pct', 'dd_level_1_pct',
                  'dd_level_2_pct', 'event_size_cut_pct')
_INT_COLUMNS = ('max_trades_per_day', 'max_open_positions', 'max_consecutive_losses', 'stop_cooldown_minutes')


def _count(column: str, value: str) -> int:
    return int(op.get_bind().execute(sa.text(f"SELECT count(*) FROM risk_settings WHERE {column} = {value}")).scalar() or 0)


def upgrade() -> None:
    op.execute("CREATE TABLE risk_settings_def0_backup AS SELECT * FROM risk_settings")
    for column in _FLOAT_COLUMNS:
        op.alter_column('risk_settings', column, existing_type=sa.Float(), nullable=True, server_default=None)
    for column in _INT_COLUMNS:
        op.alter_column('risk_settings', column, existing_type=sa.Integer(), nullable=True, server_default=None)
    counts = {
        'capital': _count('capital', '100000'), 'risk_per_trade_pct': _count('risk_per_trade_pct', '0.5'),
        'max_daily_loss_pct': _count('max_daily_loss_pct', '3'), 'max_trades_per_day': _count('max_trades_per_day', '20'),
        'max_open_positions': _count('max_open_positions', '3'), 'max_consecutive_losses': _count('max_consecutive_losses', '4'),
        'max_portfolio_risk_pct': _count('max_portfolio_risk_pct', '6'), 'stop_cooldown_minutes': _count('stop_cooldown_minutes', '30'),
        'dd_level_1_pct': _count('dd_level_1_pct', '5'), 'dd_level_2_pct': _count('dd_level_2_pct', '10'),
        'event_size_cut_pct': _count('event_size_cut_pct', '50'),
    }
    op.execute("UPDATE risk_settings SET capital = NULL WHERE capital = 100000")
    op.execute("UPDATE risk_settings SET risk_per_trade_pct = NULL WHERE risk_per_trade_pct = 0.5")
    op.execute("UPDATE risk_settings SET max_daily_loss_pct = NULL WHERE max_daily_loss_pct = 3")
    op.execute("UPDATE risk_settings SET max_trades_per_day = NULL WHERE max_trades_per_day = 20")
    op.execute("UPDATE risk_settings SET max_open_positions = NULL WHERE max_open_positions = 3")
    op.execute("UPDATE risk_settings SET max_consecutive_losses = NULL WHERE max_consecutive_losses = 4")
    op.execute("UPDATE risk_settings SET max_portfolio_risk_pct = NULL WHERE max_portfolio_risk_pct = 6")
    op.execute("UPDATE risk_settings SET stop_cooldown_minutes = NULL WHERE stop_cooldown_minutes = 30")
    op.execute("UPDATE risk_settings SET dd_level_1_pct = NULL WHERE dd_level_1_pct = 5")
    op.execute("UPDATE risk_settings SET dd_level_2_pct = NULL WHERE dd_level_2_pct = 10")
    op.execute("UPDATE risk_settings SET event_size_cut_pct = NULL WHERE event_size_cut_pct = 50")
    print("DEF-0: risk_settings values set to NULL (old platform value, now not set) per field: "
          + ", ".join(f"{k}={v}" for k, v in counts.items()) + " - every row kept in risk_settings_def0_backup")
    op.alter_column('trade_plans', 'risk_cap', existing_type=sa.Numeric(18, 2), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE trade_plans SET risk_cap = risk_amount WHERE risk_cap IS NULL")
    op.alter_column('trade_plans', 'risk_cap', existing_type=sa.Numeric(18, 2), nullable=False)
    op.execute("UPDATE risk_settings SET capital = b.capital, risk_per_trade_pct = b.risk_per_trade_pct, "
               "max_daily_loss_pct = b.max_daily_loss_pct, max_trades_per_day = b.max_trades_per_day, "
               "max_open_positions = b.max_open_positions, max_consecutive_losses = b.max_consecutive_losses, "
               "max_portfolio_risk_pct = b.max_portfolio_risk_pct, stop_cooldown_minutes = b.stop_cooldown_minutes, "
               "dd_level_1_pct = b.dd_level_1_pct, dd_level_2_pct = b.dd_level_2_pct, event_size_cut_pct = b.event_size_cut_pct "
               "FROM risk_settings_def0_backup b WHERE b.id = risk_settings.id")
    op.execute("UPDATE risk_settings SET capital = COALESCE(capital, 100000), risk_per_trade_pct = COALESCE(risk_per_trade_pct, 0.5), "
               "max_daily_loss_pct = COALESCE(max_daily_loss_pct, 3), max_trades_per_day = COALESCE(max_trades_per_day, 20), "
               "max_open_positions = COALESCE(max_open_positions, 3), max_consecutive_losses = COALESCE(max_consecutive_losses, 4), "
               "max_portfolio_risk_pct = COALESCE(max_portfolio_risk_pct, 6), stop_cooldown_minutes = COALESCE(stop_cooldown_minutes, 30), "
               "dd_level_1_pct = COALESCE(dd_level_1_pct, 5), dd_level_2_pct = COALESCE(dd_level_2_pct, 10), "
               "event_size_cut_pct = COALESCE(event_size_cut_pct, 50)")
    for column, default in (('max_portfolio_risk_pct', '6.0'), ('dd_level_1_pct', '5.0'), ('dd_level_2_pct', '10.0'),
                            ('event_size_cut_pct', '50.0')):
        op.alter_column('risk_settings', column, existing_type=sa.Float(), nullable=False, server_default=default)
    for column in ('capital', 'risk_per_trade_pct', 'max_daily_loss_pct'):
        op.alter_column('risk_settings', column, existing_type=sa.Float(), nullable=False)
    op.alter_column('risk_settings', 'stop_cooldown_minutes', existing_type=sa.Integer(), nullable=False, server_default='30')
    for column in ('max_trades_per_day', 'max_open_positions', 'max_consecutive_losses'):
        op.alter_column('risk_settings', column, existing_type=sa.Integer(), nullable=False)
    op.execute("DROP TABLE risk_settings_def0_backup")

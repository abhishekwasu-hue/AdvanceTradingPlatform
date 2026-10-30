"""M64 (owner order 30 Oct + amendment): Settings > Risk limits - every check a switch, OFF unless the trader saved it.

New nullable columns on risk_settings (NULL = not set): max_lots_per_order, daily_loss_rupees, risk_per_trade_rupees,
loss_cooldown_minutes, margin_safety_pct, and limits_switches_json - per check whether it is ON and whether the trader
saved it himself: {"enabled": [...], "saved": [...], "notice": true}.

Existing rows: a check is ON (and saved) where one of its values is stored - the values the trader saved himself (DEF-0
d0f0a1b2c3d4 already set every old platform default to NULL; a minimum reward:risk equal to the old built-in 1.2 reads as
not set, TT12-4). Every other check is OFF. No stored value changes. "notice": true marks the row for the one-time
notice "Risk limits are now off by default - turn on the ones you want" (app/me/notices.py). Tenants with no row had
every limit off already; nothing is written for them. The count of rows and checks switched on is printed.

Run after 15:30 IST (owner, DECISIONS 25 Oct).

Revision ID: 1f3a5c7e9b20
Revises: f4b6d8a0c2e3
Create Date: 2026-10-30 23:40:00
"""
import json
import math
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '1f3a5c7e9b20'
down_revision: Union[str, Sequence[str], None] = 'f4b6d8a0c2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
# Z11: new nullable columns only; the UPDATE fills the NEW limits_switches_json column - the previous release never reads it
# and keeps checking every stored value exactly as before.
ROLLBACK_SAFE = True
ROLLBACK_SAFE_REASON = ("adds nullable columns; the only UPDATE writes the new limits_switches_json column, which the previous "
                        "release never reads - its stored limit values are unchanged")

_NEW = (('max_lots_per_order', sa.Integer()), ('daily_loss_rupees', sa.Float()), ('risk_per_trade_rupees', sa.Float()),
        ('loss_cooldown_minutes', sa.Integer()), ('margin_safety_pct', sa.Float()), ('limits_switches_json', sa.Text()))

# check id -> its columns (app/risk_engine/risk_limits.py CHECKS, kept here so the migration never imports app code)
_CHECKS = {
    'max_open_positions': ('max_open_positions',), 'daily_loss': ('max_daily_loss_pct',),
    'max_entries_per_day': ('max_trades_per_day',), 'loss_cooldown': ('max_consecutive_losses',),
    'min_reward_risk': ('min_risk_reward',), 'risk_sizing': ('risk_per_trade_pct',),
    'portfolio_risk': ('max_portfolio_risk_pct',), 'stop_cooldown': ('stop_cooldown_minutes',),
    'drawdown': ('dd_level_1_pct', 'dd_level_2_pct'), 'event_rules': ('event_size_cut_pct',),
}
_LEGACY_ORG_MIN_RR = 1.2
# A stored 0 here was a real limit (the engine read it as "no new entry at all"), so it counts as saved.
_ZERO_IS_A_LIMIT = ('max_trades_per_day', 'max_open_positions', 'max_consecutive_losses', 'max_daily_loss_pct')


def _set(column: str, value) -> bool:
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(number) or number < 0 or (number == 0 and column not in _ZERO_IS_A_LIMIT):
        return False
    return not (column == 'min_risk_reward' and number == _LEGACY_ORG_MIN_RR)


def upgrade() -> None:
    for name, kind in _NEW:
        op.add_column('risk_settings', sa.Column(name, kind, nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, max_open_positions, max_daily_loss_pct, max_trades_per_day, max_consecutive_losses, min_risk_reward, "
        "risk_per_trade_pct, max_portfolio_risk_pct, stop_cooldown_minutes, dd_level_1_pct, dd_level_2_pct, event_size_cut_pct "
        "FROM risk_settings")).mappings().all()
    switched_on = 0
    for row in rows:
        on = sorted(check for check, cols in _CHECKS.items() if any(_set(c, row[c]) for c in cols))
        switched_on += len(on)
        bind.execute(sa.text("UPDATE risk_settings SET limits_switches_json = :switches WHERE id = :id").bindparams(
            switches=json.dumps({"enabled": on, "notice": True, "saved": on}, sort_keys=True), id=row['id']))
    print(f"M64: risk_settings rows given switches: {len(rows)}; checks left ON (values the trader saved): {switched_on}; "
          "every other check OFF")


def downgrade() -> None:
    for name, _kind in reversed(_NEW):
        op.drop_column('risk_settings', name)

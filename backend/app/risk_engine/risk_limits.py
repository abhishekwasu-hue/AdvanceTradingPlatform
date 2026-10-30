"""M64 (owner order 30 Oct + amendment): Settings > Risk limits - the one place the trader's order-time risk checks live.

Every check has a switch and its value. **Every switch is OFF by default** (amendment): with all of them off, an entry is
the lots the trader typed (default 1) and only the broker / exchange checks (margin available, freeze-quantity split, lot
multiple, market open) and the kill switch stand between the order and the market. The trader turns on only what he
wants. Exits are never checked (ADR-0004): nothing here is asked on an exit.

Storage: the organisation's `risk_settings` row (app/db/models.py `RiskSettingsRecord`) - the row "My limits" has always
saved to and the one every entry path reads (`app/risk_engine/routes.get_tenant_risk_config`). The values stay in their
columns; `limits_switches_json` holds, per check, whether it is ON and whether the trader saved it himself:
``{"enabled": [...], "saved": [...]}``. A switched-off check keeps its value (turning it back on restores it) but is read
as "not set" by the engine (`apply_switches`). A row with no switches (none written yet) reads "on where a value is set",
the rule the M64 migration (alembic 1f3a5c7e9b20) applies to every existing row: what the trader saved stays on.

Org / admin ceilings (the risk hierarchy rows, app/risk_engine/hierarchy.py, and the platform ceilings in
app/platform/controls.py) stay above these and are OFF by default for every organisation (owner, 30 Oct). When one
applies, the block message says "Ask your admin" instead of "Change limit".

RM-3 block messages: `block()` builds one plain sentence with the numbers, the link to the exact check
(``/settings/risk-limits#<check>``) and, for a size limit, the smaller size the trader may place instead. `log_block()`
writes every block to the append-only `risk_events` table (who / what / which limit), which the journal reads.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from app.core.models import RiskConfig

PAGE_PATH = "/settings/risk-limits"
ADMIN_TEXT = "Ask your admin"
EVENT_RULE_PREFIX = "RISK_LIMIT:"          # risk_events.rule_type of a Risk limits block (the hierarchy's are plain types)


@dataclass(frozen=True)
class Field:
    key: str                  # the RiskConfig / risk_settings field
    label: str
    unit: str
    low: float
    high: float
    whole: bool = False
    optional: bool = False    # may stay empty while the check is on (the other field of the check is enough)


@dataclass(frozen=True)
class Check:
    id: str
    label: str
    help: str                 # one line, shown behind the (?)
    fields: tuple[Field, ...]
    group: str = "main"       # "main" (the owner's list) or "more" (the guardian's rules)
    any_field: bool = False   # on with at least one of its fields set (a % or a rupee amount)


CHECKS: tuple[Check, ...] = (
    Check("max_lots_per_order", "Max lots per order", "An order above this size is not placed; you are offered this size instead.",
          (Field("max_lots_per_order", "Lots", "lots", 1, 100_000, whole=True),)),
    Check("max_open_positions", "Max open positions", "No new entry while you hold this many. A spread counts as one position.",
          (Field("max_open_positions", "Positions", "positions", 1, 200, whole=True),)),
    Check("daily_loss", "Daily loss limit", "Once today's loss reaches it, no new entry today. Exits always work.",
          (Field("max_daily_loss_pct", "% of account", "%", 0.1, 100, optional=True),
           Field("daily_loss_rupees", "Rupees", "₹", 1, 1_000_000_000, optional=True)), any_field=True),
    Check("max_entries_per_day", "Max new entries per day", "No new entry after this many today. A spread counts as one entry.",
          (Field("max_trades_per_day", "Entries", "entries", 1, 500, whole=True),)),
    Check("loss_cooldown", "Cool-down after losses in a row", "After this many losses in a row, new entries pause for these minutes.",
          (Field("max_consecutive_losses", "Losses in a row", "losses", 1, 50, whole=True),
           Field("loss_cooldown_minutes", "Pause", "minutes", 1, 1440, whole=True, optional=True))),
    Check("min_reward_risk", "Minimum reward:risk", "A trade whose target pays less than this for each rupee at risk is not placed.",
          (Field("min_risk_reward", "Reward for 1 risk", "x", 0.1, 10),)),
    Check("margin_safety", "Margin safety", "Plan to use at most this share of your available margin.",
          (Field("margin_safety_pct", "Of available margin", "%", 1, 100),)),
    Check("risk_sizing", "Risk-based sizing", "The size is your risk per trade divided by the stop distance, in whole lots.",
          (Field("risk_per_trade_pct", "% of account", "%", 0.01, 100, optional=True),
           Field("risk_per_trade_rupees", "Rupees", "₹", 1, 1_000_000_000, optional=True)), any_field=True),
    Check("portfolio_risk", "Risk across open positions", "What every open stop and the new trade could lose together stays under this.",
          (Field("max_portfolio_risk_pct", "% of account", "%", 0.1, 100),), group="more"),
    Check("stop_cooldown", "Cool-down after a stop-out", "After a stop, new entries in the same symbol wait this long.",
          (Field("stop_cooldown_minutes", "Wait", "minutes", 1, 1440, whole=True),), group="more"),
    Check("drawdown", "Drawdown steps", "This far below your equity peak the size is halved; further below, new entries pause.",
          (Field("dd_level_1_pct", "Halve size at", "%", 0.1, 100, optional=True),
           Field("dd_level_2_pct", "Pause at", "%", 0.1, 100, optional=True)), group="more", any_field=True),
    Check("event_rules", "Market event days", "Event days you or your admin listed pause or cut new entries.",
          (Field("event_size_cut_pct", "Size cut when an event names none", "%", 1, 100, optional=True),), group="more"),
)
BY_ID: dict[str, Check] = {c.id: c for c in CHECKS}
FIELD_CHECK: dict[str, str] = {f.key: c.id for c in CHECKS for f in c.fields}
# A check with no value that still means something when on: the event rules apply the events' own cut / block.
VALUELESS_OK = frozenset({"event_rules"})


# --- switches -----------------------------------------------------------------------------------------------------------

# A stored 0 here was a real limit before M64 (the engine read it as "no new entry at all"; the old Risk page said so), so a
# 0 the trader saved counts as set. Everywhere else 0 means "not set".
ZERO_IS_A_LIMIT = frozenset({"max_trades_per_day", "max_open_positions", "max_consecutive_losses", "max_daily_loss_pct"})


def _has(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def is_set(key: str, value: Any) -> bool:
    """Whether a stored field value is a limit the trader set (see ZERO_IS_A_LIMIT)."""
    if key in ZERO_IS_A_LIMIT:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    return _has(value)


def values_of(record: Any) -> dict[str, Any]:
    """The stored value of every check field (None = empty). `min_risk_reward` reads as the engine does (TT12-4)."""
    from app.risk_engine.routes import org_min_rr_read
    out: dict[str, Any] = {}
    for check in CHECKS:
        for f in check.fields:
            raw = getattr(record, f.key, None) if record is not None else None
            if f.key == "min_risk_reward":
                raw = org_min_rr_read(raw) or None
            out[f.key] = raw
    return out


def derived_switches(values: dict[str, Any]) -> dict[str, list[str]]:
    """The switches of a row that has none yet: a check is ON (and counts as saved by the trader) when one of its values
    is set. The M64 migration writes exactly this for every existing row."""
    on = sorted(c.id for c in CHECKS if any(is_set(f.key, values.get(f.key)) for f in c.fields))
    return {"enabled": on, "saved": list(on)}


def switches_of(record: Any) -> dict[str, list[str]]:
    """`{"enabled": [...], "saved": [...]}` of the organisation's row (no row: every check off)."""
    if record is None:
        return {"enabled": [], "saved": []}
    raw = getattr(record, "limits_switches_json", None)
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return derived_switches(values_of(record))
    def ids(key: str) -> list[str]:
        items = data.get(key)
        return sorted({i for i in items if isinstance(i, str) and i in BY_ID}) if isinstance(items, list) else []
    return {"enabled": ids("enabled"), "saved": ids("saved")}


def migrated_notice(record: Any) -> bool:
    """Whether the M64 migration marked this row for the one-time "Risk limits are now off by default" notice (a row
    that existed before the change). A save from the page writes switches without the mark."""
    raw = getattr(record, "limits_switches_json", None) if record is not None else None
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("notice") is True


def apply_switches(cfg: RiskConfig, enabled: list[str] | set[str]) -> RiskConfig:
    """The config the engine checks with: every field of a switched-off check reads as not set (None; the minimum
    reward:risk as 0 = no minimum). Pure."""
    update: dict[str, Any] = {"event_rules": "event_rules" in enabled}
    for check in CHECKS:
        if check.id in enabled:
            continue
        for f in check.fields:
            update[f.key] = 0.0 if f.key == "min_risk_reward" else None
    return cfg.model_copy(update=update)


def events_on(record: Any) -> bool:
    """Whether the market-event rules (block / size cut on a listed event day) apply to this organisation's entries."""
    return "event_rules" in switches_of(record)["enabled"]


# --- block messages (RM-3) ----------------------------------------------------------------------------------------------

def _fmt_money(value: float) -> str:
    return f"₹{value:,.0f}"


def block(check: str, text: str, *, numbers: dict[str, Any] | None = None, smaller_lots: int | None = None,
          admin: bool = False) -> dict[str, Any]:
    """One block message as data: the plain sentence, its numbers, where to change the limit (or "Ask your admin" when an
    organisation ceiling applied) and, for a size limit, the smaller size the trader may place instead."""
    return {"check": check, "text": text, "numbers": numbers or {}, "admin": admin,
            "link": None if admin else f"{PAGE_PATH}#{check}", "link_text": ADMIN_TEXT if admin else "Change limit",
            "smaller_lots": smaller_lots}


def lots_text(n: float) -> str:
    n = int(n) if float(n).is_integer() else n
    return f"{n:g} lot" if n == 1 else f"{n:g} lots"


def max_lots_block(limit: int, asked_lots: float) -> dict[str, Any]:
    return block("max_lots_per_order", f"Max lots per order is {limit} - this order is {lots_text(asked_lots)}. Place {lots_text(limit)}?",
                 numbers={"limit": limit, "asked": asked_lots}, smaller_lots=limit)


def open_positions_block(held: int, limit: int) -> dict[str, Any]:
    return block("max_open_positions", f"You hold {held} open position{'s' if held != 1 else ''}; your limit is {limit}. "
                 "Exit one before a new entry.", numbers={"held": held, "limit": limit})


def daily_loss_block(loss: float, limit: float) -> dict[str, Any]:
    return block("daily_loss", f"Today's loss {_fmt_money(loss)} has reached your daily loss limit of {_fmt_money(limit)}. "
                 "No new entries today; exits still work.", numbers={"loss": round(loss, 2), "limit": round(limit, 2)})


def entries_block(made: int, limit: int) -> dict[str, Any]:
    return block("max_entries_per_day", f"You made {made} new entr{'ies' if made != 1 else 'y'} today; your limit is {limit}. "
                 "No new entries today; exits still work.", numbers={"made": made, "limit": limit})


def cooldown_block(losses: int, limit: int, until: str) -> dict[str, Any]:
    return block("loss_cooldown", f"{losses} losses in a row (your limit is {limit}): new entries pause {until}. Exits still work.",
                 numbers={"losses": losses, "limit": limit})


def reward_risk_block(rr: float, minimum: float) -> dict[str, Any]:
    return block("min_reward_risk", f"This trade's reward:risk is {rr:.2f}; your minimum is {minimum:.2f}.",
                 numbers={"reward_risk": round(rr, 2), "minimum": minimum})


def below_one_lot_block(cap: float, per_lot: float) -> dict[str, Any]:
    return block("risk_sizing", f"Your risk per trade of {_fmt_money(cap)} is less than one lot's risk of {_fmt_money(per_lot)} "
                 "at this stop-loss. Set a closer stop-loss, or change the limit.", numbers={"risk_per_trade": round(cap, 2), "per_lot": round(per_lot, 2)})


def no_distance_block() -> dict[str, Any]:
    return block("risk_sizing", "The stop-loss is at the entry price, so risk-based sizing cannot work out a size. Move the stop-loss.")


NO_STOP_WARNING = ("No stop-loss is set, so risk-based sizing cannot size this position: your own lots ({lots}) are used. "
                   "The loss is not limited.")


def as_text(item: dict[str, Any]) -> str:
    """The block as one line for the order trail and notifications: the sentence and where to change it."""
    where = ADMIN_TEXT if item.get("admin") else "Change limit: Settings > Risk limits"
    return f"{item['text']} ({where})"


# --- the journal's record (RM-3) ----------------------------------------------------------------------------------------

async def log_block(session: Any, *, tenant_id: int, user_id: int | None, item: dict[str, Any], symbol: str | None = None,
                    strategy_id: str | None = None, order_id: int | None = None, mode: str = "PAPER", source: str = "order") -> None:
    """Appends one `risk_events` row for a Risk limits block: who (user), what (symbol / strategy / order) and which
    limit, with its numbers. Never raises: a block that cannot be logged is still a block."""
    import logging

    from app.db.models import RiskEventRecord
    try:
        numbers = item.get("numbers") or {}
        current = next((float(v) for k, v in numbers.items() if k in ("asked", "held", "loss", "made", "losses", "reward_risk", "per_lot")
                        and isinstance(v, (int, float))), 0.0)
        limit = next((float(v) for k, v in numbers.items() if k in ("limit", "minimum", "risk_per_trade") and isinstance(v, (int, float))), 0.0)
        session.add(RiskEventRecord(
            tenant_id=tenant_id, strategy_id=strategy_id, symbol=(symbol or "")[:50] or None, rule_id=None,
            rule_type=(EVENT_RULE_PREFIX + str(item.get("check")))[:40], scope="ADMIN" if item.get("admin") else "USER",
            current_value=current, limit_value=limit, severity="WARNING", action="BLOCK_ORDER", status="BLOCK",
            reason=str(item.get("text"))[:300], order_id=order_id,
            metadata_json=json.dumps({"user_id": user_id, "mode": mode, "source": source, "check": item.get("check"),
                                      "numbers": numbers, "smaller_lots": item.get("smaller_lots")}, default=str),
        ))
        await session.flush()
    except Exception:  # noqa: BLE001 - the record is bookkeeping; the block holds either way
        logging.getLogger(__name__).exception("Risk limits block could not be logged")


# --- the page (GET / PUT /api/risk-limits) -------------------------------------------------------------------------------

def view(record: Any, ceilings: dict[str, float]) -> dict[str, Any]:
    """What Settings > Risk limits shows: every check with its switch, values, unit, bounds and one-line help."""
    values = values_of(record)
    sw = switches_of(record)
    checks = []
    for c in CHECKS:
        checks.append({
            "id": c.id, "label": c.label, "help": c.help, "group": c.group, "enabled": c.id in sw["enabled"], "saved": c.id in sw["saved"],
            "fields": [{"key": f.key, "label": f.label, "unit": f.unit, "min": f.low, "max": min(f.high, _ceiling(f.key, ceilings)),
                        "whole": f.whole, "optional": f.optional, "value": values.get(f.key)} for f in c.fields],
        })
    return {"checks": checks, "capital": getattr(record, "capital", None) if record is not None else None,
            "loss_streak_pause": getattr(record, "loss_streak_pause", None) or "next_session",
            "on": len(sw["enabled"]), "exits": "Exits are never checked: stops, targets and Exit now always work.", "page": PAGE_PATH}


def _ceiling(key: str, ceilings: dict[str, float]) -> float:
    """The platform ceiling of a field (an admin setting), or no ceiling."""
    mapped = {"risk_per_trade_pct": "risk_per_trade_pct", "max_daily_loss_pct": "max_daily_loss_pct",
              "max_portfolio_risk_pct": "max_portfolio_risk_pct", "dd_level_2_pct": "dd_level_2_pct"}.get(key)
    return float(ceilings[mapped]) if mapped and mapped in ceilings else math.inf


def plan_save(record: Any, body: dict[str, Any], ceilings: dict[str, float]) -> tuple[dict[str, Any], dict[str, list[str]], list[str]]:
    """(the field values to store, the new switches, the problems in plain words) for a PUT. Pure.

    `body["checks"]` maps a check id to `{"enabled": bool, "values": {field: number | null}}`; checks left out keep
    their switch and values. A check switched on needs its value (one of the two for a % / rupee pair); a switched-off
    check keeps the value it had (or the one sent). Every check in the body counts as saved by the trader."""
    problems: list[str] = []
    values = values_of(record)
    sw = switches_of(record)
    enabled, saved = set(sw["enabled"]), set(sw["saved"])
    sent = body.get("checks") or {}
    if not isinstance(sent, dict):
        return {}, sw, ["The limits could not be read. Reload the page and try again."]
    for check_id, item in sent.items():
        check = BY_ID.get(check_id)
        if check is None or not isinstance(item, dict):
            problems.append("One of the limits is not known. Reload the page and try again.")
            continue
        new_values = item.get("values") or {}
        for f in check.fields:
            if f.key not in new_values:
                continue
            raw = new_values[f.key]
            if raw is None or raw == "":
                values[f.key] = None
                continue
            if not isinstance(raw, (int, float)) or isinstance(raw, bool) or not math.isfinite(raw):
                problems.append(f"{check.label}: enter a number.")
                continue
            top = min(f.high, _ceiling(f.key, ceilings))
            if raw < f.low or raw > f.high:
                problems.append(f"{check.label}: {f.label.lower()} must be between {f.low:g} and {f.high:g}.")
                continue
            if raw > top:
                problems.append(f"{check.label}: your admin caps {f.label.lower()} at {top:g}. {ADMIN_TEXT}.")
                continue
            if f.whole and not float(raw).is_integer():
                problems.append(f"{check.label}: enter a whole number.")
                continue
            values[f.key] = int(raw) if f.whole else float(raw)
        want_on = bool(item.get("enabled"))
        if want_on:
            required = [f for f in check.fields if not f.optional]
            has_value = (any(_has(values.get(f.key)) for f in check.fields) if check.any_field or not required
                         else all(_has(values.get(f.key)) for f in required))
            if not has_value and check.id not in VALUELESS_OK:
                problems.append(f"{check.label}: enter a value to switch it on.")
                continue
            enabled.add(check.id)
        else:
            enabled.discard(check.id)
        saved.add(check.id)
    lvl1, lvl2 = values.get("dd_level_1_pct"), values.get("dd_level_2_pct")
    if _has(lvl1) and _has(lvl2) and lvl1 > lvl2:
        problems.append("Drawdown steps: the halve-size level must not be beyond the pause level.")
    stored = {k: values[k] for k in values}
    if "min_risk_reward" in stored:
        from app.risk_engine.routes import org_min_rr_to_store
        stored["min_risk_reward"] = org_min_rr_to_store(float(stored["min_risk_reward"])) if _has(stored["min_risk_reward"]) else 0.0
    return stored, {"enabled": sorted(enabled), "saved": sorted(saved)}, problems


def switches_json(switches: dict[str, list[str]]) -> str:
    return json.dumps({"enabled": sorted(switches.get("enabled", [])), "saved": sorted(switches.get("saved", []))}, sort_keys=True)


__all__ = ["CHECKS", "BY_ID", "FIELD_CHECK", "PAGE_PATH", "ADMIN_TEXT", "switches_of", "apply_switches", "derived_switches", "values_of",
           "events_on", "block", "as_text", "log_block", "view", "plan_save", "switches_json", "NO_STOP_WARNING"]

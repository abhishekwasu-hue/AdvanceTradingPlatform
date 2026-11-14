"""Runner S4: what the link writes into the runner's local store, and what it mirrors back (docs/design/USER_RUNNER.md
section 4).

- `apply_rules`: HIS saved rules from ATP (rules R-2 / R-3) into the local SQLite store, with ATP's own ids (so a
  deployment's `custom:<id>` strategy and the mirror's deployment refs mean the same on both sides). ATP owns the rule
  columns; the runner owns its run state (what it evaluated, the last signal bar, errors) and never takes ATP's. A
  deployment no longer in his saved list is STOPPED here (never deleted: its open position keeps its exits). No
  platform control, admin kill switch or maintenance block is in the snapshot or written here (rule R-1).
- `apply_reference`: the F&O ban list (into the runner-mode seam, `core/runtime_mode.set_ban_list`) and the contracts of
  his symbols. Until a list arrives, LIVE F&O entries stay refused in plain words (S2); exits never read it.
- `mirror_page`: one page of read-only copies (orders, positions, audit summaries) in S3's exact upload schema.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.runtime_mode as runtime_mode
from app.db.models import (
    CustomStrategyRecord,
    InstrumentRecord,
    OrderRecord,
    RiskSettingsRecord,
    StrategyDeploymentRecord,
    Tenant,
    TradeRecord,
    User,
    UserPrefsRecord,
)

STOPPED_IN_ATP = "Stopped: no longer in your saved deployments in ATP"
MIRROR_PAGE = 200                 # rows per kind in one upload (S3 caps 500 and 256 KB)
_DEPLOYMENT_RUN_STATE = frozenset({"id", "tenant_id", "created_by", "last_evaluated_at", "last_signal_at", "last_error",
                                   "consecutive_failures", "wait_reason", "data_failures", "waiting_for_data_since",
                                   "last_route", "broker_account_id", "created_at", "updated_at"})
_RISK_LOCAL = frozenset({"id", "tenant_id", "updated_by", "created_at", "updated_at"})


def _columns(model: Any) -> set[str]:
    return {c.name for c in model.__table__.columns}


def _date(value: Any) -> Any:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    return value


async def apply_rules(session: AsyncSession, snapshot: dict[str, Any]) -> dict[str, int]:
    """Writes his saved rules into the local store. Returns counts for the plain log line."""
    tenant_id, user_id = int(snapshot["org_ref"]), int(snapshot["user_ref"])
    if await session.get(Tenant, tenant_id) is None:
        session.add(Tenant(id=tenant_id, name="My runner"))
    if await session.get(User, user_id) is None:
        # a local stand-in for the user (no e-mail, no password: nobody logs in to the runner's store)
        session.add(User(id=user_id, tenant_id=tenant_id, email=f"user-{user_id}@runner.local", hashed_password="-"))
    await session.flush()

    counts = {"strategies": 0, "deployments": 0, "stopped": 0, "settings": 0}
    for row in snapshot.get("strategies") or []:
        record = await session.get(CustomStrategyRecord, int(row["id"]))
        if record is None:
            record = CustomStrategyRecord(id=int(row["id"]), tenant_id=tenant_id, user_id=user_id, name=row["name"],
                                          config_json=row["config_json"])
            session.add(record)
        record.name, record.config_json = row["name"], row["config_json"]
        record.origin = row.get("origin") or record.origin or "user"
        counts["strategies"] += 1

    rule_columns = _columns(StrategyDeploymentRecord) - _DEPLOYMENT_RUN_STATE
    seen: set[int] = set()
    for row in snapshot.get("deployments") or []:
        dep_id = int(row["id"])
        seen.add(dep_id)
        record = await session.get(StrategyDeploymentRecord, dep_id)
        if record is None:
            record = StrategyDeploymentRecord(id=dep_id, tenant_id=tenant_id, created_by=user_id, strategy_id=row["strategy_id"],
                                              symbol=row["symbol"])
            session.add(record)
        for name in rule_columns & set(row):                      # a column a newer ATP adds is ignored here
            setattr(record, name, row[name])
        counts["deployments"] += 1
    gone = await session.scalars(select(StrategyDeploymentRecord).where(
        StrategyDeploymentRecord.tenant_id == tenant_id, StrategyDeploymentRecord.status.in_(("ACTIVE", "PAUSED"))))
    for record in gone:
        if record.id not in seen:
            record.status, record.pause_reason = "STOPPED", STOPPED_IN_ATP
            counts["stopped"] += 1

    settings = snapshot.get("settings") or {}
    prefs: dict[str, str] = settings.get("prefs") or {}
    stored = {r.namespace: r for r in await session.scalars(select(UserPrefsRecord).where(
        UserPrefsRecord.tenant_id == tenant_id, UserPrefsRecord.user_id == user_id))}
    for namespace, value in prefs.items():
        record = stored.get(namespace)
        if record is None:
            session.add(UserPrefsRecord(tenant_id=tenant_id, user_id=user_id, namespace=namespace, value_json=value))
        else:
            record.value_json = value
        counts["settings"] += 1
    for namespace, record in stored.items():
        if namespace not in prefs:
            await session.delete(record)                          # not set in ATP: the defaults, as there
    risk = settings.get("risk")
    local = await session.scalar(select(RiskSettingsRecord).where(RiskSettingsRecord.tenant_id == tenant_id))
    if risk is None:
        if local is not None:
            await session.delete(local)
    else:
        if local is None:
            local = RiskSettingsRecord(tenant_id=tenant_id)
            session.add(local)
        for name in (_columns(RiskSettingsRecord) - _RISK_LOCAL) & set(risk):
            setattr(local, name, risk[name])
        counts["settings"] += 1
    await session.commit()
    return counts


async def apply_reference(session: AsyncSession, reference: dict[str, Any]) -> dict[str, int]:
    ban = reference.get("ban_list")
    if isinstance(ban, dict) and isinstance(ban.get("underlyings"), list):
        runtime_mode.set_ban_list(ban["underlyings"])
    rows = reference.get("instruments") or []
    await session.execute(delete(InstrumentRecord).where(InstrumentRecord.instrument_type.in_(("FUT", "CE", "PE"))))
    fields = _columns(InstrumentRecord) - {"id", "synced_at"}
    for row in rows:
        session.add(InstrumentRecord(**{k: _date(v) if k == "expiry" else v for k, v in row.items() if k in fields}))
    await session.commit()
    names = runtime_mode.ban_list()
    return {"ban list": -1 if names is None else len(names), "contracts": len(rows)}


# --- the mirror ------------------------------------------------------------------------------------------------------

def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def _status(text: str | None) -> str:
    word = re.sub(r"[^A-Z_]", "_", (text or "").upper())[:20]
    return word if re.fullmatch(r"[A-Z_]{1,20}", word) else "UNKNOWN"


def _exchange(text: str | None) -> str:
    word = (text or "").upper()
    return word if re.fullmatch(r"[A-Z_]{2,10}", word) else "UNKNOWN"


def _first_reason(raw: str | None) -> str | None:
    try:
        reasons = json.loads(raw or "[]")
    except ValueError:
        return None
    if isinstance(reasons, list) and reasons:
        return str(reasons[0])[:300]
    return None


def _deployment_id(order: OrderRecord) -> int | None:
    match = re.fullmatch(r"deployment:(\d+):.*", order.idempotency_key or "")
    return int(match.group(1)) if match else None


async def mirror_page(session: AsyncSession, audit_path: Path, since: str | None,
                      audit_after: int) -> tuple[dict[str, Any], str | None, int]:
    """(upload, new `since`, new audit seq). Orders / positions changed at or after `since` (S3 keeps them idempotent),
    open positions always, and audit rows after `audit_after`."""
    from app.runner import audit as audit_log
    since_dt = datetime.fromisoformat(since) if since else None
    deps = {d.id: d for d in await session.scalars(select(StrategyDeploymentRecord))}

    order_q = select(OrderRecord).order_by(OrderRecord.updated_at, OrderRecord.id).limit(MIRROR_PAGE)
    if since_dt is not None:
        order_q = order_q.where(OrderRecord.updated_at >= since_dt)
    orders, newest = [], since_dt
    for o in await session.scalars(order_q):
        dep = deps.get(_deployment_id(o) or -1)
        style = (dep.order_style if dep is not None else None) or "MARKET"
        updated = o.updated_at or o.created_at
        orders.append({"ref": f"order:{o.id}", "deployment_ref": f"deployment:{dep.id}" if dep is not None else None,
                       "mode": o.mode if o.mode in ("PAPER", "LIVE") else "PAPER", "symbol": o.symbol[:60],
                       "exchange": _exchange(dep.exchange if dep is not None else None),
                       "side": "SELL" if (o.direction or "").upper() == "SHORT" else "BUY",
                       "order_type": style if style in ("MARKET", "LIMIT", "SL", "SL-M") else "MARKET",
                       "quantity": max(0.0, float(o.quantity or 0)), "status": _status(o.status),
                       "reason": _first_reason(o.reasons_json), "placed_at": _iso(o.created_at), "updated_at": _iso(updated)})
        newest = updated if newest is None or _aware(updated) > _aware(newest) else newest

    trade_q = select(TradeRecord).order_by(TradeRecord.id).limit(MIRROR_PAGE)
    if since_dt is not None:
        trade_q = trade_q.where(or_(TradeRecord.exit_time.is_(None), TradeRecord.entry_time >= since_dt,
                                    TradeRecord.exit_time >= since_dt, TradeRecord.mark_time >= since_dt))
    positions = []
    for t in await session.scalars(trade_q):
        dep = deps.get(t.deployment_id or -1)
        stamps = [s for s in (t.entry_time, t.mark_time, t.exit_time) if s is not None]
        updated = max(stamps, key=_aware)
        quantity = float(t.quantity or 0) * (-1 if (t.direction or "").upper() == "SHORT" else 1)
        positions.append({"ref": f"trade:{t.id}", "deployment_ref": f"deployment:{t.deployment_id}" if t.deployment_id else None,
                          "mode": t.mode if t.mode in ("PAPER", "LIVE") else "PAPER", "symbol": t.symbol[:60],
                          "exchange": _exchange(dep.exchange if dep is not None else None),
                          "status": "OPEN" if t.exit_time is None else "CLOSED", "quantity": quantity,
                          "average_price": t.entry_price, "last_price": t.exit_price if t.exit_time else t.mark_price,
                          "realised_pnl": t.pnl if t.exit_time else None, "exit_reason": (t.exit_reason or None) and t.exit_reason[:120],
                          "opened_at": _iso(t.entry_time), "closed_at": _iso(t.exit_time), "updated_at": _iso(updated)})
        newest = updated if newest is None or _aware(updated) > _aware(newest) else newest

    rows = [r for r in audit_log.read(audit_path) if int(r.get("seq") or 0) > audit_after][:MIRROR_PAGE]
    audit = [{"seq": int(r["seq"]), "action": _action(r.get("kind")), "summary": _summary(r.get("data")),
              "row_hash": r["hash"], "prev_hash": r.get("prev") or None, "occurred_at": r["ts"]} for r in rows]
    seq = audit[-1]["seq"] if audit else audit_after
    return {"orders": orders, "positions": positions, "audit": audit}, _iso(newest) if newest else since, seq


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _action(kind: Any) -> str:
    word = re.sub(r"[^a-z0-9_.:-]", "_", str(kind or "row").lower())[:60]
    return word or "row"


_SUMMARY_KEYS = ("symbol", "mode", "direction", "status", "executed", "reason", "on", "deployment_id", "trade_id", "order_id")


def _summary(data: Any) -> str:
    """A short line from the row's own fields (no secrets are ever in the audit log; see audit.py)."""
    if not isinstance(data, dict):
        return ""
    parts = [f"{k}={data[k]}" for k in _SUMMARY_KEYS if k in data and data[k] is not None]
    return " ".join(parts)[:300]


__all__ = ["apply_rules", "apply_reference", "mirror_page", "STOPPED_IN_ATP"]

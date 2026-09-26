"""Phase D2: compliance exports.

Regulators and auditors ask for "everything you did between these dates" as a file, not as an
API. This module produces CSV or JSON exports of the four record sets that matter for that -
the hash-chained audit trail, the order trail (every attempt, with its algo tag), filled
trades and login attempts - scoped to one tenant (owner export) or the whole platform
(SUPER_ADMIN export), for a date range.

Two properties make the file itself evidence rather than just data:

* every export carries the SHA-256 of its exact bytes (`X-Content-SHA256` header and the
  `export_generated` audit row), so a file handed over later can be shown to be the one the
  platform produced, and
* the audit-log export includes each row's `prev_hash` and `hash` and the platform's own
  chain-verification verdict at export time, so the recipient can re-verify the chain offline
  with the documented hash recipe (`app/audit/log.py`).
"""
import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.log import verify_audit_chain
from app.db.models import AuditLogRecord, LoginEventRecord, OrderRecord, TradeRecord, User

MAX_ROWS = 50_000
FORMATS = ("csv", "json")


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class Dataset:
    name: str
    model: Any
    columns: Sequence[str]
    row: Callable[[Any, Optional[str]], Dict[str, Any]]
    tenant_column: Any
    time_column: Any
    joins_user: bool = True


def _audit_row(r: AuditLogRecord, email: Optional[str]) -> Dict[str, Any]:
    return {
        "id": r.id, "tenant_id": r.tenant_id, "user_id": r.user_id, "user_email": email, "event": r.event,
        "detail": r.detail, "created_at": _iso(r.created_at), "prev_hash": r.prev_hash, "hash": r.hash,
    }


def _order_row(r: OrderRecord, email: Optional[str]) -> Dict[str, Any]:
    return {
        "id": r.id, "tenant_id": r.tenant_id, "user_id": r.user_id, "user_email": email, "mode": r.mode,
        "strategy_id": r.strategy_id, "symbol": r.symbol, "direction": r.direction, "quantity": r.quantity,
        "status": r.status, "broker_order_id": r.broker_order_id, "algo_tag": r.algo_tag, "trade_id": r.trade_id,
        "idempotency_key": r.idempotency_key, "reasons": "; ".join(json.loads(r.reasons_json or "[]")),
        "created_at": _iso(r.created_at), "updated_at": _iso(r.updated_at),
    }


def _trade_row(r: TradeRecord, email: Optional[str]) -> Dict[str, Any]:
    return {
        "id": r.id, "tenant_id": r.tenant_id, "user_id": r.user_id, "user_email": email, "mode": r.mode,
        "strategy_id": r.strategy_id, "symbol": r.symbol, "direction": r.direction, "quantity": r.quantity,
        "entry_time": _iso(r.entry_time), "entry_price": r.entry_price, "stop_loss": r.stop_loss,
        "target1": r.target1, "target2": r.target2, "exit_time": _iso(r.exit_time), "exit_price": r.exit_price,
        "exit_reason": r.exit_reason, "pnl": r.pnl, "charges": r.charges, "broker_order_id": r.broker_order_id,
        "sl_order_id": r.sl_order_id, "deployment_id": r.deployment_id,
    }


def _login_row(r: LoginEventRecord, email: Optional[str]) -> Dict[str, Any]:
    return {
        "id": r.id, "tenant_id": r.tenant_id, "user_id": r.user_id, "email": r.email, "success": r.success,
        "reason": r.reason, "ip_address": r.ip_address, "user_agent": r.user_agent, "created_at": _iso(r.created_at),
    }


DATASETS: Dict[str, Dataset] = {
    "audit-logs": Dataset(
        "audit-logs", AuditLogRecord,
        ("id", "tenant_id", "user_id", "user_email", "event", "detail", "created_at", "prev_hash", "hash"),
        _audit_row, AuditLogRecord.tenant_id, AuditLogRecord.created_at,
    ),
    "orders": Dataset(
        "orders", OrderRecord,
        ("id", "tenant_id", "user_id", "user_email", "mode", "strategy_id", "symbol", "direction", "quantity", "status",
         "broker_order_id", "algo_tag", "trade_id", "idempotency_key", "reasons", "created_at", "updated_at"),
        _order_row, OrderRecord.tenant_id, OrderRecord.created_at,
    ),
    "trades": Dataset(
        "trades", TradeRecord,
        ("id", "tenant_id", "user_id", "user_email", "mode", "strategy_id", "symbol", "direction", "quantity",
         "entry_time", "entry_price", "stop_loss", "target1", "target2", "exit_time", "exit_price", "exit_reason",
         "pnl", "charges", "broker_order_id", "sl_order_id", "deployment_id"),
        _trade_row, TradeRecord.tenant_id, TradeRecord.entry_time,
    ),
    "login-events": Dataset(
        "login-events", LoginEventRecord,
        ("id", "tenant_id", "user_id", "email", "success", "reason", "ip_address", "user_agent", "created_at"),
        _login_row, LoginEventRecord.tenant_id, LoginEventRecord.created_at, joins_user=False,
    ),
}


@dataclass
class Export:
    dataset: str
    fmt: str
    content: bytes
    row_count: int
    sha256: str
    filename: str
    media_type: str
    date_from: Optional[date]
    date_to: Optional[date]
    chain_intact: Optional[bool] = None


def _bounds(date_from: Optional[date], date_to: Optional[date]):
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc) if date_from else None
    # `to` is inclusive: the whole of that day.
    end = datetime.combine(date_to, time.max, tzinfo=timezone.utc) if date_to else None
    return start, end


async def build_export(
    session: AsyncSession, dataset_name: str, fmt: str, *, tenant_id: Optional[int],
    date_from: Optional[date] = None, date_to: Optional[date] = None, limit: int = MAX_ROWS,
    generated_by: Optional[str] = None,
) -> Export:
    """`tenant_id=None` means the whole platform (SUPER_ADMIN only - the routes enforce that)."""
    if dataset_name not in DATASETS:
        raise ValueError(f"Unknown dataset {dataset_name!r}; one of {sorted(DATASETS)}")
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format {fmt!r}; one of {FORMATS}")
    if date_from and date_to and date_from > date_to:
        raise ValueError("'from' must not be after 'to'")
    ds = DATASETS[dataset_name]
    limit = max(1, min(limit, MAX_ROWS))

    if ds.joins_user:
        query = select(ds.model, User.email).join(User, User.id == ds.model.user_id, isouter=True)
    else:
        query = select(ds.model, ds.model.email)
    if tenant_id is not None:
        query = query.where(ds.tenant_column == tenant_id)
    start, end = _bounds(date_from, date_to)
    if start is not None:
        query = query.where(ds.time_column >= start)
    if end is not None:
        query = query.where(ds.time_column <= end)
    result = await session.execute(query.order_by(ds.model.id.asc()).limit(limit))
    rows: List[Dict[str, Any]] = [ds.row(record, email) for record, email in result]

    chain_intact: Optional[bool] = None
    if dataset_name == "audit-logs":
        chain_intact, _ = await verify_audit_chain(session)

    generated_at = datetime.now(timezone.utc)
    manifest = {
        "dataset": dataset_name, "scope": "platform" if tenant_id is None else f"tenant:{tenant_id}",
        "from": date_from.isoformat() if date_from else None, "to": date_to.isoformat() if date_to else None,
        "row_count": len(rows), "truncated": len(rows) >= limit, "generated_at": generated_at.isoformat(),
        "generated_by": generated_by,
    }
    if chain_intact is not None:
        manifest["audit_chain_intact_at_export"] = chain_intact
        manifest["hash_recipe"] = "sha256(prev_hash|tenant_id|user_id|event|detail|created_at_utc_naive_iso)"

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(ds.columns), extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
        # The manifest travels as comment lines at the end so the file stays a plain CSV.
        for key, value in manifest.items():
            buf.write(f"# {key}={json.dumps(value)}\n")
        content = buf.getvalue().encode("utf-8")
        media_type = "text/csv; charset=utf-8"
    else:
        content = json.dumps({"manifest": manifest, "rows": rows}, indent=2, default=str).encode("utf-8")
        media_type = "application/json"

    sha = hashlib.sha256(content).hexdigest()
    scope = "platform" if tenant_id is None else f"tenant{tenant_id}"
    span = f"{date_from or 'start'}_{date_to or 'now'}"
    filename = f"{dataset_name}_{scope}_{span}_{generated_at.strftime('%Y%m%dT%H%M%SZ')}.{fmt}"
    return Export(dataset_name, fmt, content, len(rows), sha, filename, media_type, date_from, date_to, chain_intact)

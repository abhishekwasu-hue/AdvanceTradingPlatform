"""Z10-c (G6): the probes the tenancy sweep sends (tests/test_z10c_tenancy_sweep.py).

- `plant_rows`: one row of tenant A in every tenant- or user-owned table, all with the same primary key (PROBE_ID) and a
  marker string in every text column. So `/api/<anything>/{id}` called with PROBE_ID names "tenant A's row of whatever
  this route reads", whichever table that is, and any response that carries the marker leaked A's data. Foreign keys
  point at PROBE_ID too, i.e. at A's own rows of the referenced tables.
- `snapshot`: the planted rows' contents, to prove a request of another tenant changed none of them.
- `body_for`: a minimal JSON body that satisfies the route's request schema (from the app's OpenAPI document), so a
  request gets past validation to the code that has to refuse it.
- `path_for`: the route's path with every parameter filled - integers with PROBE_ID, names with a plausible value.
"""
from __future__ import annotations

import importlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, LargeBinary, Numeric, String, Text, delete, select
from sqlalchemy.sql.schema import Table

from app.db.base import Base

PROBE_ID = 970_001
# Tables never planted: the identity and key material of the tenant itself (planting would break tenant A rather than
# probe it), append-only chains, and rows whose mere presence changes platform behaviour for everyone.
NOT_PLANTED = frozenset({
    "users", "tenant_keys", "user_sessions", "audit_logs", "login_events", "kill_switches", "subscriptions",
    "md_tenant_candles", "email_verifications", "password_resets", "mfa_backup_codes", "tenant_invites",
})

# Path parameters that are not row ids: the value the probe sends.
PATH_VALUES: Dict[str, str] = {
    "name": "upstox", "broker_name": "upstox", "provider": "openai", "symbol": "RELIANCE", "underlying": "NIFTY",
    "channel_type": "telegram", "strategy_id": "ema_rsi_scalper_1m", "sector": "Energy", "token": "z10c-not-a-token",
    "webhook_token": "z10c-not-a-token", "action": "pause", "kind": "ema", "timeframe": "1day", "key": "z10c",
    "version_number": "1", "exchange": "NSE", "flag": "phase0_paper_only", "instrument_key": "NSE_EQ|RELIANCE",
    "day": "2026-09-25", "week": "2026-W39", "study_id": "z10c-study", "dataset": "trades", "concept_id": "z10c-concept",
}


def plantable_tables() -> List[Table]:
    importlib.import_module("app.db.models")   # registers every table on Base.metadata
    tables = []
    for table in Base.metadata.tables.values():
        cols = table.c.keys()
        pk = [c.name for c in table.primary_key]
        if table.name in NOT_PLANTED or pk != ["id"] or not ({"tenant_id", "user_id"} & set(cols)):
            continue
        if not isinstance(table.c.id.type, Integer):
            continue
        tables.append(table)
    return sorted(tables, key=lambda t: t.name)


def _value(column, *, tenant_id: int, user_id: int, marker: str, now: datetime) -> Any:
    name = column.name
    if name == "id":
        return PROBE_ID
    if name in ("tenant_id", "creator_tenant_id"):
        return tenant_id
    if name in ("user_id", "created_by", "owner_user_id", "decided_by", "updated_by"):
        return user_id
    kind = column.type
    if column.foreign_keys:
        return PROBE_ID if isinstance(kind, Integer) else marker
    if isinstance(kind, Boolean):
        return False
    if isinstance(kind, DateTime):
        return now
    if isinstance(kind, Date):
        return now.date()
    if isinstance(kind, (Integer,)):
        return 1
    if isinstance(kind, (Float, Numeric)):
        return 1.0
    if isinstance(kind, LargeBinary):
        return marker.encode()
    if isinstance(kind, (String, Text)):
        if name.endswith("_json"):
            return json.dumps({"z10c": marker})
        return marker
    return None


async def plant_rows(session, *, tenant_id: int, user_id: int, marker: str) -> List[str]:
    """Inserts tenant A's probe row into every plantable table (one transaction per table; a table whose row cannot
    be built from types alone is skipped and reported). Returns the planted table names."""
    now = datetime.now(timezone.utc)
    planted = []
    for table in plantable_tables():
        row = {}
        for column in table.c:
            value = _value(column, tenant_id=tenant_id, user_id=user_id, marker=marker, now=now)
            if value is not None or not column.nullable:
                row[column.name] = value
        try:
            await session.execute(table.insert().values(**row))
            await session.commit()
            planted.append(table.name)
        except Exception:  # noqa: BLE001 - a CHECK / unique constraint the generic row does not meet
            await session.rollback()
    return planted


async def remove_rows(session, tables: List[str]) -> None:
    for name in tables:
        table = Base.metadata.tables[name]
        await session.execute(delete(table).where(table.c.id == PROBE_ID))
    await session.commit()


async def snapshot(session, tables: List[str]) -> Dict[str, Optional[Tuple]]:
    out: Dict[str, Optional[Tuple]] = {}
    for name in tables:
        table = Base.metadata.tables[name]
        row = (await session.execute(select(table).where(table.c.id == PROBE_ID))).first()
        out[name] = tuple(row) if row is not None else None
    return out


# --- request bodies -------------------------------------------------------------------------------------------------

def _resolve(schema: Dict, components: Dict) -> Dict:
    while "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def instance(schema: Dict, components: Dict, depth: int = 0) -> Any:
    """A small value valid for a JSON schema (as FastAPI/pydantic emit them)."""
    schema = _resolve(schema, components)
    if depth > 6:
        return None
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema and schema.get("default") is not None:
        return schema["default"]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if _resolve(o, components).get("type") != "null"]
            return instance(options[0], components, depth + 1) if options else None
    if "allOf" in schema:
        merged: Dict = {}
        for part in schema["allOf"]:
            merged.update(_resolve(part, components))
        return instance(merged, components, depth + 1)
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {name: instance(props[name], components, depth + 1) for name in schema.get("required", []) if name in props}
    if kind == "array":
        item = instance(schema.get("items", {}), components, depth + 1)
        return [item] * max(1, int(schema.get("minItems", 1)))
    if kind == "integer":
        return max(int(schema.get("minimum", schema.get("exclusiveMinimum", 0) + 1 if "exclusiveMinimum" in schema else 1)), 1)
    if kind == "number":
        low = schema.get("minimum", schema.get("exclusiveMinimum", 0.5))
        return float(low) + (0.5 if "exclusiveMinimum" in schema else 0.0) or 1.0
    if kind == "boolean":
        return False
    if kind == "string":
        fmt = schema.get("format")
        if fmt == "date-time":
            return datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc).isoformat()
        if fmt == "date":
            return date(2026, 9, 25).isoformat()
        if fmt == "email":
            return "z10c@example.com"
        if "pattern" in schema:
            return _pattern_sample(schema["pattern"]) or "x"
        return "x" * max(1, int(schema.get("minLength", 1)))
    return None


def _pattern_sample(pattern: str) -> Optional[str]:
    """The first alternative of a simple `^(a|b|c)$` pattern - the shape every enum-like pattern in the API has."""
    found = re.fullmatch(r"\^?\(?(?:\?i\))?\(?([A-Za-z0-9_ .:-]+)(?:\|[^)]*)?\)?\$?", pattern)
    return found.group(1) if found else None


def body_for(openapi: Dict, method: str, path: str) -> Optional[Any]:
    operation = openapi.get("paths", {}).get(path, {}).get(method.lower())
    if not operation or "requestBody" not in operation:
        return None
    content = operation["requestBody"].get("content", {})
    schema = (content.get("application/json") or {}).get("schema")
    if schema is None:
        return None
    return instance(schema, openapi.get("components", {}).get("schemas", {}))


def path_for(path: str, int_params: Iterable[str] = (), probe_id: int = PROBE_ID) -> str:
    """`int_params`: the route's integer path parameters (row ids) - they get PROBE_ID; the others a PATH_VALUES name."""
    ints = set(int_params)

    def fill(match: "re.Match[str]") -> str:
        name = match.group(1).split(":")[0]
        if name in ints:
            return str(probe_id)
        if name in PATH_VALUES:
            return PATH_VALUES[name]
        raise KeyError(f"No probe value for path parameter '{name}' in {path} - add one to PATH_VALUES")
    return re.sub(r"\{([^}]+)\}", fill, path)


def int_path_params(dependant) -> List[str]:
    return [p.name for p in dependant.path_params if getattr(p.field_info, "annotation", None) is int]

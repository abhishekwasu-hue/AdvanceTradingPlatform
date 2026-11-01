"""M71 PL-7b: data-use windows of a tenant's private research data (the "sealed / contaminated" rule).

The windows are NOT written in this public code: the seed import reads them from the private data's own policy and
stores them per tenant (`data_use_windows`). Two policy forms are read, the first found wins:

- `data_policy.json` at the data root: {"windows": [{"kind": "sealed", "start": "YYYY-MM-DD", "end": null,
  "scope": "NIFTY", "label": "...", "use": "..."}]} (kinds: sealed, contaminated, in_sample, validation);
- `README.md` at the data root: the markdown table under a heading that names the data-use rules, with the columns
  Window | Dates | Use. A window named HOLDOUT / sealed -> sealed, CONTAMINATED -> contaminated, IS -> in_sample,
  VAL -> validation; "YYYY", "YYYY-MM" or "YYYY-MM-DD" bounds ("A - B", or "A onward" for an open end). Such a table
  names no symbol, so it applies to TRADE_DATA_POLICY_SCOPE.

The rule (the same as the Trade research policy the private data records): sealed and contaminated rows are refused by
every calibration / research / optimisation path (app.backtest.data_policy); charts, live reading, backtests shown to
the user and screeners display them. Windows belong to ONE tenant: no other organisation's runs are affected.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.data_policy import UseWindow
from app.core import config
from app.db.models import DataUseWindowRecord

KINDS = ("sealed", "contaminated", "in_sample", "validation")
POLICY_JSON = "data_policy.json"
POLICY_README = "README.md"
_BOUND = re.compile(r"\d{4}(?:-\d{2}(?:-\d{2})?)?")
_OPEN_END = re.compile(r"\b(onward|onwards|from|later|after)\b", re.IGNORECASE)
_HEADING = re.compile(r"^#+\s*.*(data[- ]use|data policy)", re.IGNORECASE)


@dataclass(frozen=True)
class Policy:
    source: str                  # the policy file's name inside the data root
    sha256: str
    windows: tuple[UseWindow, ...]


def _first_day(token: str) -> date:
    parts = [int(p) for p in token.split("-")]
    return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)


def _last_day(token: str) -> date:
    parts = [int(p) for p in token.split("-")]
    if len(parts) == 3:
        return date(*parts)
    if len(parts) == 2:
        return date(parts[0], parts[1], calendar.monthrange(parts[0], parts[1])[1])
    return date(parts[0], 12, 31)


def kind_of(label: str) -> str | None:
    up = " ".join((label or "").upper().replace("*", "").split())
    if "HOLDOUT" in up or "SEALED" in up:
        return "sealed"
    if "CONTAMINATED" in up:
        return "contaminated"
    if up.startswith("VAL"):
        return "validation"
    if up in ("IS", "IN-SAMPLE", "IN SAMPLE", "IN_SAMPLE"):
        return "in_sample"
    return None


def parse_span(text: str) -> tuple[date, date | None] | None:
    """'2015 - 2021' -> (2015-01-01, 2021-12-31); 'A onward' -> (A, None); None when no bound is found."""
    tokens = _BOUND.findall(text or "")
    if not tokens:
        return None
    start = _first_day(tokens[0])
    if len(tokens) == 1:
        return (start, None) if _OPEN_END.search(text) else (start, _last_day(tokens[0]))
    return start, _last_day(tokens[1])


def parse_readme(text: str, scopes: Sequence[str]) -> list[UseWindow]:
    """The data-use table of a README (see the module docstring); every other table is ignored."""
    out: list[UseWindow] = []
    in_section, header = False, None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            in_section, header = bool(_HEADING.match(line)), None
            continue
        if not in_section or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        row = dict(zip(header, cells))
        kind = kind_of(row.get("window", cells[0]))
        span = parse_span(row.get("dates", cells[1] if len(cells) > 1 else ""))
        if kind is None or span is None:
            continue
        for scope in scopes:
            out.append(UseWindow(kind, span[0], span[1], scope, row.get("window", cells[0])[:60]))
    return out


def parse_json(text: str, scopes: Sequence[str]) -> list[UseWindow]:
    data = json.loads(text)
    out: list[UseWindow] = []
    for w in data.get("windows", []) if isinstance(data, dict) else []:
        kind = str(w.get("kind", "")).strip().lower()
        if kind not in KINDS or not w.get("start"):
            continue
        start = date.fromisoformat(str(w["start"]))
        end = date.fromisoformat(str(w["end"])) if w.get("end") else None
        for scope in ([str(w["scope"]).strip().upper()] if w.get("scope") else list(scopes)):
            out.append(UseWindow(kind, start, end, scope or "*", str(w.get("label") or kind)[:60]))
    return out


def load_policy(root: Path, scopes: Sequence[str] | None = None) -> Policy | None:
    """The private data's policy (JSON first, else the README table), or None when it states none."""
    scopes = [s.upper() for s in (scopes or config.TRADE_DATA_POLICY_SCOPE)]
    for name, parser in ((POLICY_JSON, parse_json), (POLICY_README, parse_readme)):
        path = root / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        windows = parser(raw.decode("utf-8", errors="replace"), scopes)
        if windows:
            return Policy(name, hashlib.sha256(raw).hexdigest(), tuple(windows))
    return None


async def store(session: AsyncSession, tenant_id: int, policy: Policy) -> dict[str, int]:
    """Replaces the tenant's windows from this policy file with the policy's current set (idempotent). Does not commit."""
    old = {(r.kind, r.scope, r.start_date): r for r in await session.scalars(select(DataUseWindowRecord).where(
        DataUseWindowRecord.tenant_id == tenant_id, DataUseWindowRecord.policy_source == policy.source))}
    keep: set[tuple[str, str, date]] = set()
    added = changed = 0
    for w in policy.windows:
        k = (w.kind, w.scope, w.start)
        keep.add(k)
        row = old.get(k)
        if row is None:
            session.add(DataUseWindowRecord(tenant_id=tenant_id, kind=w.kind, scope=w.scope, start_date=w.start, end_date=w.end,
                                            label=w.label, policy_source=policy.source, policy_sha256=policy.sha256))
            added += 1
        elif row.end_date != w.end or row.policy_sha256 != policy.sha256:
            row.end_date, row.label, row.policy_sha256 = w.end, w.label, policy.sha256
            changed += 1
    gone = [r.id for k, r in old.items() if k not in keep]
    if gone:
        await session.execute(delete(DataUseWindowRecord).where(DataUseWindowRecord.id.in_(gone)))
    return {"added": added, "changed": changed, "removed": len(gone)}


def underlying_of(symbol_or_key: str) -> str:
    """'NIFTY' / 'NIFTY 50' / 'NSE_INDEX:NIFTY50' / 'NFO:NIFTY:CE:...' / 'NSE:RELIANCE' -> the underlying name."""
    raw = " ".join((symbol_or_key or "").strip().upper().split())
    if ":" in raw:
        venue, _, rest = raw.partition(":")
        if venue in ("NFO", "BFO", "MCX"):
            return rest.split(":", 1)[0]
        if venue in ("NSE_INDEX", "BSE_INDEX"):
            from app.instruments import master
            from app.market_lake import index_history as dh
            for name in master.INDEX_SYMBOLS:
                if dh.lake_key_for(name) == raw:
                    return name
            return rest
        return rest
    from app.backtest.options import underlying_name
    return underlying_name(raw)[0]


async def windows_for(session: AsyncSession, tenant_id: int | None, symbol: str, *, refused_only: bool = False) -> list[UseWindow]:
    """The tenant's windows that apply to `symbol` (its underlying, or "*"). None / no tenant -> none."""
    if not tenant_id or int(tenant_id) <= 0:
        return []
    name = underlying_of(symbol)
    rows = await session.scalars(select(DataUseWindowRecord).where(
        DataUseWindowRecord.tenant_id == int(tenant_id), DataUseWindowRecord.scope.in_((name, "*"))).order_by(DataUseWindowRecord.start_date))
    out = [UseWindow(r.kind, r.start_date, r.end_date, r.scope, r.label or "") for r in rows]
    return [w for w in out if w.refused] if refused_only else out


def tags(windows: Sequence[UseWindow], first: date, last: date) -> list[dict[str, Any]]:
    """The windows a stored range [first, last] overlaps, as display tags (charts may show them; nothing is hidden)."""
    out: list[dict[str, Any]] = []
    for w in windows:
        if last >= w.start and (w.end is None or first <= w.end):
            out.append({"kind": w.kind, "from": max(first, w.start).isoformat(),
                        "to": (last if w.end is None else min(last, w.end)).isoformat(), "refused_for_research": w.refused})
    return out

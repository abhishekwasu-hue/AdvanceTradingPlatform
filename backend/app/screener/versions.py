"""LAB-18 (AMW-7, owner decision 2026-10-23): immutable server-side versions of a saved screen.

* Every save of a screen (`POST` / `PUT /api/screener/screens`) that changes it writes a new `screen_versions` row: the
  scan body (name, canonical text, AST, timeframe, parameters), a plain change line, who saved it and when. The `PUT`
  still updates the current copy in place; versions are never updated or restored over: "open an older version" loads
  it into the builder and the next save writes a new version.
* At most MAX_VERSIONS per screen; past that the oldest are pruned.
* The history a device kept before this (frontend `screener/versions.ts`, OPEN_QUESTIONS LAB-18) is uploaded once per
  screen (`import_device`), validated and capped; `screens.versions_imported_at` is the marker that makes a repeat a
  no-op.
* Behind `screener_copilot` (with `screener_v2`), like the device history of #412; the page adds `ui_v2`.

The change line is the trader's own words about his own scan (what he added or removed); it is never signal text and
names no order.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScreenRecord, ScreenVersionRecord
from app.screener import nodes
from app.screener import stages as stage_split

FLAG = "screener_copilot"
MAX_VERSIONS = 50            # per screen; the oldest are pruned past it
MAX_IMPORT = 20              # the device kept at most 20 per scan (versions.ts MAX_VERSIONS)
MAX_LINE = 160
MAX_CHANGE = 600             # the column's length
MAX_BUILDER = 32_000         # characters of the builder blocks' JSON
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Body:
    """A screen's body as one version stores it."""
    name: str
    source_text: str
    ast_json: str
    ast_version: str
    base_tf: str
    params_json: str

    @staticmethod
    def of(row: ScreenRecord) -> "Body":
        return Body(row.name, row.source_text, row.ast_json, row.ast_version, row.base_tf, row.params_json or "{}")

    def sha256(self) -> str:
        blob = json.dumps([self.name, self.source_text, self.base_tf, json.loads(self.params_json or "{}")],
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def conditions(self) -> List[str]:
        try:
            return [c["text"] for c in stage_split.split(nodes.from_json(json.loads(self.ast_json)))]
        except (ValueError, KeyError, TypeError):
            return [self.source_text] if self.source_text else []


def _clean_line(text: str) -> str:
    return _CONTROL.sub(" ", text).strip()[:MAX_LINE]


def _count(n: int) -> str:
    return f"{n} condition" if n == 1 else f"{n} conditions"


def _uniq(xs: List[str]) -> List[str]:
    return list(dict.fromkeys(x for x in xs if x))


def describe_change(prev: Optional[Body], prev_builder: Optional[Dict[str, Any]], now: Body,
                    now_builder: Optional[Dict[str, Any]]) -> str:
    """What changed from `prev` to `now` in plain lines - the same lines as the page's `changes()` (versions.ts): each
    added / removed block in the builder's plain words when both versions carry the builder blocks, else counts. Never
    the formula text."""
    if prev is None:
        return "First saved version."
    out: List[str] = []
    if prev_builder and now_builder:
        before, after = prev_builder["texts"], now_builder["texts"]
        added = _uniq([now_builder["summaries"][i] for i, t in enumerate(after) if t not in before])
        removed = _uniq([prev_builder["summaries"][i] for i, t in enumerate(before) if t not in after])
        if added:
            out.append(_clean_line(f"Added: {'; '.join(added)}"))
        if removed:
            out.append(_clean_line(f"Removed: {'; '.join(removed)}"))
    else:
        before, after = prev.conditions(), now.conditions()
        added, removed = [c for c in after if c not in before], [c for c in before if c not in after]
        if added:
            out.append(f"Added {_count(len(added))}.")
        if removed:
            out.append(f"Removed {_count(len(removed))}.")
    if prev.base_tf != now.base_tf:
        out.append(f"Timeframe: {prev.base_tf} to {now.base_tf}")
    if not out and before != after:
        out.append("The same blocks in a new order.")
    if not out and prev.params_json != now.params_json:
        out.append("Changed a setting.")
    if prev.name != now.name:
        out.append(_clean_line(f"Name: “{prev.name}” to “{now.name}”"))
    return "\n".join(out)[:MAX_CHANGE] if out else "No change in the conditions."


def clean_builder(builder: Any) -> Optional[Dict[str, Any]]:
    """The builder blocks as the page saved them ({stages, texts, summaries}), so "Open this version" restores the same
    blocks and the change line can use their plain words; None when missing, the wrong shape or too large. Never used
    for running: a run always uses the stored canonical text."""
    if not isinstance(builder, dict):
        return None
    stages, texts, summaries = builder.get("stages"), builder.get("texts"), builder.get("summaries")
    if not isinstance(stages, list) or not isinstance(texts, list) or not isinstance(summaries, list):
        return None
    if len(texts) != len(summaries) or not all(isinstance(t, str) for t in texts) \
            or not all(isinstance(x, str) for x in summaries):
        return None
    out = {"stages": stages, "texts": texts, "summaries": [_clean_line(x) for x in summaries]}
    return out if len(json.dumps(out, separators=(",", ":"))) <= MAX_BUILDER else None


def _builder_of(v: Optional[ScreenVersionRecord]) -> Optional[Dict[str, Any]]:
    if v is None or not v.builder_json:
        return None
    try:
        return clean_builder(json.loads(v.builder_json))
    except ValueError:
        return None


async def _lock(session: AsyncSession, row: ScreenRecord) -> None:
    """Serialises version writes on one screen (two tabs saving, or importing, at once): the screen row is locked and
    re-read, so the next version number and the import marker are current (Postgres; SQLite runs one writer anyway)."""
    await session.execute(select(ScreenRecord).where(ScreenRecord.id == row.id).with_for_update()
                          .execution_options(populate_existing=True))


async def _latest(session: AsyncSession, screen_id: int) -> Optional[ScreenVersionRecord]:
    return await session.scalar(select(ScreenVersionRecord).where(ScreenVersionRecord.screen_id == screen_id)
                                .order_by(ScreenVersionRecord.created_at.desc(), ScreenVersionRecord.version.desc()).limit(1))


async def _next_number(session: AsyncSession, screen_id: int) -> int:
    top = await session.scalar(select(func.max(ScreenVersionRecord.version)).where(ScreenVersionRecord.screen_id == screen_id))
    return int(top or 0) + 1


def _row(row: ScreenRecord, body: Body, number: int, change: str, builder: Optional[Dict[str, Any]], origin: str,
         user_id: Optional[int], at: datetime) -> ScreenVersionRecord:
    return ScreenVersionRecord(tenant_id=row.tenant_id, screen_id=row.id, version=number, name=body.name,
                               source_text=body.source_text, ast_json=body.ast_json, ast_version=body.ast_version,
                               base_tf=body.base_tf, params_json=body.params_json, body_sha256=body.sha256(),
                               change=change[:MAX_CHANGE], origin=origin,
                               builder_json=json.dumps(builder, separators=(",", ":")) if builder else None, created_by=user_id,
                               created_at=at)


async def prune(session: AsyncSession, screen_id: int, keep: int = MAX_VERSIONS) -> int:
    """Deletes the oldest versions past `keep`; how many went."""
    ids = list(await session.scalars(select(ScreenVersionRecord.id).where(ScreenVersionRecord.screen_id == screen_id)
                                     .order_by(ScreenVersionRecord.created_at.desc(), ScreenVersionRecord.version.desc())
                                     .offset(keep)))
    if ids:
        await session.execute(delete(ScreenVersionRecord).where(ScreenVersionRecord.id.in_(ids)))
    return len(ids)


async def record(session: AsyncSession, row: ScreenRecord, user_id: Optional[int],
                 builder: Any = None) -> Optional[ScreenVersionRecord]:
    """After a save: a new version when the screen differs from its latest version (a save that changes nothing writes
    nothing). `row` must have its id (flushed). The caller commits."""
    if row.id is None:
        await session.flush()
    await _lock(session, row)
    body = Body.of(row)
    latest = await _latest(session, row.id)
    if latest is not None and latest.body_sha256 == body.sha256():
        return None
    prev = Body(latest.name, latest.source_text, latest.ast_json, latest.ast_version, latest.base_tf,
                latest.params_json) if latest is not None else None
    blocks = clean_builder(builder)
    line = describe_change(prev, _builder_of(latest), body, blocks)
    version = _row(row, body, await _next_number(session, row.id), line, blocks, "save", user_id,
                   datetime.now(timezone.utc))
    session.add(version)
    await session.flush()
    await prune(session, row.id)
    return version


def _parse_at(value: str) -> Optional[datetime]:
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


@dataclass
class ImportResult:
    imported: int
    skipped: int
    already: bool


async def import_device(session: AsyncSession, row: ScreenRecord, user_id: Optional[int], items: Sequence[Dict[str, Any]],
                        compile_text: Any, now: Optional[datetime] = None) -> ImportResult:
    """The one upload of a device's history for this screen. Each item ({at, name, base_tf, texts, stages, summaries})
    is validated by compiling its blocks (`compile_text(text, base_tf) -> (ast, validated)`); an item that does not
    compile, is dated in the future or repeats the one before it is skipped. Imported versions keep their saved time,
    so they sit before the versions this server wrote (which all came after the device kept its own). The screen's
    marker makes a repeat a no-op. The caller commits."""
    await _lock(session, row)
    if row.versions_imported_at is not None:
        return ImportResult(0, 0, True)
    now = now or datetime.now(timezone.utc)
    first_server = await session.scalar(select(func.min(ScreenVersionRecord.created_at))
                                        .where(ScreenVersionRecord.screen_id == row.id))
    if first_server is not None and first_server.tzinfo is None:
        first_server = first_server.replace(tzinfo=timezone.utc)          # SQLite hands back naive UTC
    ordered = sorted((i for i in items if isinstance(i, dict)), key=lambda i: str(i.get("at", "")))
    number = await _next_number(session, row.id)
    prev: Optional[Body] = None
    prev_blocks: Optional[Dict[str, Any]] = None
    imported = skipped = 0
    for item in ordered[-MAX_IMPORT:]:
        at = _parse_at(str(item.get("at", "")))
        texts = item.get("texts")
        base_tf = str(item.get("base_tf", ""))
        name = _CONTROL.sub(" ", str(item.get("name", ""))).strip()[:120] or row.name
        if at is None or at > now or (first_server is not None and at >= first_server) or not isinstance(texts, list) \
                or not texts or not all(isinstance(t, str) and t.strip() for t in texts) or base_tf not in nodes.TF_MINUTES:
            skipped += 1
            continue
        ast, validated = compile_text(" AND ".join(f"({t})" for t in texts), base_tf)
        if ast is None or not validated.ok:
            skipped += 1
            continue
        body = Body(name, nodes.to_text(ast), json.dumps(nodes.to_json(ast)), nodes.VERSION, base_tf, "{}")
        if prev is not None and prev.sha256() == body.sha256():
            skipped += 1
            continue
        blocks = clean_builder(item)
        session.add(_row(row, body, number, describe_change(prev, prev_blocks, body, blocks), blocks, "device", user_id, at))
        number += 1
        imported += 1
        prev, prev_blocks = body, blocks
    skipped += max(0, len(ordered) - MAX_IMPORT) + (len(items) - len(ordered))
    row.versions_imported_at = now
    await session.flush()
    await prune(session, row.id)
    return ImportResult(imported, skipped, False)


async def listing(session: AsyncSession, screen_id: int) -> List[ScreenVersionRecord]:
    """Oldest first (the order the page numbers them in)."""
    return list(await session.scalars(select(ScreenVersionRecord).where(ScreenVersionRecord.screen_id == screen_id)
                                      .order_by(ScreenVersionRecord.created_at, ScreenVersionRecord.version)))


def summary(v: ScreenVersionRecord, number: int, latest: bool) -> Dict[str, Any]:
    """One version in the list: what the Versions dialog shows."""
    body = Body(v.name, v.source_text, v.ast_json, v.ast_version, v.base_tf, v.params_json)
    return {"version": v.version, "number": number, "latest": latest, "name": v.name, "base_tf": v.base_tf,
            "conditions": len(body.conditions()), "change": [line for line in v.change.split("\n") if line],
            "origin": v.origin, "created_at": v.created_at.isoformat() if v.created_at else None}


def detail(v: ScreenVersionRecord, number: int, latest: bool) -> Dict[str, Any]:
    """One version in full: the body to load into the builder."""
    builder = _builder_of(v)
    return {**summary(v, number, latest), "text": v.source_text, "ast": json.loads(v.ast_json), "ast_version": v.ast_version,
            "params": json.loads(v.params_json or "{}"), "builder": builder}

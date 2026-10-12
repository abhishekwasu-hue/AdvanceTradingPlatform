"""Z10-a: a one-off audit of what is stored against the validator of today.

Z10-a made the validator stricter: a last-bar-only function inside a history-reading one (`Count(5, PatternBullish())`)
and an expression as a function's setting (`Supertrend(10, ATR(5) / 2)`) are refused now. A saved screen or an alert
rule written before that would stop running (the bar-close engine reports "the rule's condition no longer validates").
`audit_stored` lists every such row, with the problems, so the operator can tell its owner before the beta. It reads
only; it changes nothing.

    docker compose exec backend python scripts/audit_screens.py [--json]     # or GET /api/admin/screener/audit
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlertRuleRecord, ScreenRecord
from app.screener import compile_screen


def _problems(source: Any, base_tf: str, params: Dict[str, Any]) -> List[str]:
    try:
        ast, validated = compile_screen(source, base_tf=base_tf, params=params)
    except Exception as exc:  # noqa: BLE001 - a row that cannot even be read is reported, not raised
        return [f"cannot be read: {type(exc).__name__}: {exc}"]
    return [] if validated.ok and ast is not None else [p.message for p in validated.problems] or ["does not validate"]


def _json(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


async def audit_stored(session: AsyncSession) -> Dict[str, Any]:
    """Saved screens (not archived) and instrument alert rules whose condition no longer validates. A screen alert rule
    runs its screen, so it is listed through the screen (with the rules that use it)."""
    screens = (await session.execute(select(ScreenRecord).where(ScreenRecord.archived.is_(False)))).scalars().all()
    rules = (await session.execute(select(AlertRuleRecord))).scalars().all()
    rules_by_screen: Dict[int, List[int]] = {}
    for rule in rules:
        if rule.kind == "screen" and rule.screen_id is not None:
            rules_by_screen.setdefault(rule.screen_id, []).append(rule.id)
    failing: List[Dict[str, Any]] = []
    for s in screens:
        problems = _problems(_json(s.ast_json, {}), s.base_tf, _json(s.params_json, {}))
        if problems:
            failing.append({"kind": "screen", "id": s.id, "tenant_id": s.tenant_id, "name": s.name, "text": s.source_text,
                            "base_tf": s.base_tf, "problems": problems, "alert_rules": sorted(rules_by_screen.get(s.id, []))})
    checked_rules = 0
    for r in rules:
        if r.kind == "screen":
            continue
        checked_rules += 1
        problems = _problems(r.condition_text or "", r.base_tf, {})
        if problems:
            failing.append({"kind": "alert_rule", "id": r.id, "tenant_id": r.tenant_id, "name": r.name, "text": r.condition_text,
                            "base_tf": r.base_tf, "status": r.status, "problems": problems})
    return {"screens_checked": len(screens), "alert_rules_checked": checked_rules, "failing": failing}


__all__ = ["audit_stored"]

"""Part D6 (rule IN-SEBI.golive.checklist): the go-live items the code cannot prove by itself.

The item list is rule-set data (`items`): each is either
- `evidence` - a human records where the evidence lives (ISO 27001 / SOC 2 certificate, CERT-In VAPT report, strategy
  white-box / black-box classification filed with the broker, DPO contact, breach runbook); it is `ok` while the latest
  record is within `validity_days` (or its own `valid_until`), else `warn`; or
- `auto` - checked from the platform's own state: `log_retention` (login history kept at least `log_retention_days`),
  `ra_gate` (AI trade ideas stay unpublished - flag `marketplace_ai_listings` off - unless a SEBI Research Analyst
  registration is on record for this item).
These feed the platform (SUPER_ADMIN) readiness checklist as LIVE-scope items. Missing evidence is `warn`, never `todo`:
it must not block a PAPER go-live. Nothing here changes trading.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance import rules
from app.db.models import ComplianceEvidenceRecord

RULE = "IN-SEBI.golive.checklist"
GROUPS = ("vendor", "strategy", "ai", "dpdp")
AUTO_CHECKS = ("log_retention", "ra_gate")


class EvidenceError(ValueError):
    pass


@dataclass
class GoLiveItem:
    id: str
    group: str
    title: str
    status: str            # ok / warn
    detail: str
    kind: str              # evidence / auto


def items() -> List[dict]:
    out = list(rules.load().param(RULE, "items", []) or [])
    for item in out:
        if item.get("group") not in GROUPS or ("evidence" in item) == ("auto" in item):
            raise ValueError(f"{RULE}: item {item.get('id')!r} needs a known group and exactly one of evidence/auto")
        if "auto" in item and item["auto"] not in AUTO_CHECKS:
            raise ValueError(f"{RULE}: unknown auto check {item['auto']!r}")
    return out


async def latest_evidence(session: AsyncSession) -> Dict[str, ComplianceEvidenceRecord]:
    latest: Dict[str, ComplianceEvidenceRecord] = {}
    for row in await session.scalars(select(ComplianceEvidenceRecord).order_by(ComplianceEvidenceRecord.recorded_at, ComplianceEvidenceRecord.id)):
        latest[row.item_id] = row
    return latest


def _expiry(item: dict, record: ComplianceEvidenceRecord) -> Optional[date]:
    if record.valid_until is not None:
        return record.valid_until
    days = item.get("validity_days")
    return (record.recorded_at.date() + timedelta(days=int(days))) if days else None


def _evidence_status(item: dict, record: Optional[ComplianceEvidenceRecord], today: date):
    if record is None:
        return "warn", "no evidence recorded"
    until = _expiry(item, record)
    if until is not None and until < today:
        return "warn", f"evidence expired on {until.isoformat()} ({record.reference})"
    return "ok", record.reference + (f" (valid until {until.isoformat()})" if until else "")


async def _auto_status(session: AsyncSession, item: dict, record: Optional[ComplianceEvidenceRecord], today: date):
    if item["auto"] == "log_retention":
        from app.retention.policy import load_policy
        keep = int(rules.load().param(RULE, "log_retention_days", 0) or 0)
        days = load_policy().login_events_days
        return ("ok" if days >= keep else "warn"), f"login history kept {days} days (rule: at least {keep})"
    from app.platform.controls import flag_enabled   # ra_gate
    if not await flag_enabled(session, "marketplace_ai_listings"):
        return "ok", "AI trade ideas are not published (marketplace_ai_listings off)"
    status, detail = _evidence_status(item, record, today)
    return status, ("AI listings are on - SEBI RA registration: " + detail)


async def evaluate(session: AsyncSession, today: Optional[date] = None) -> List[GoLiveItem]:
    today = today or date.today()
    evidence = await latest_evidence(session)
    out: List[GoLiveItem] = []
    for item in items():
        record = evidence.get(item["id"])
        if "auto" in item:
            status, detail = await _auto_status(session, item, record, today)
        else:
            status, detail = _evidence_status(item, record, today)
        out.append(GoLiveItem(item["id"], item["group"], item["title"], status, detail, "auto" if "auto" in item else "evidence"))
    return out


async def record_evidence(session: AsyncSession, item_id: str, reference: str, valid_until: Optional[date],
                          user_id: Optional[int]) -> ComplianceEvidenceRecord:
    known = {i["id"] for i in items() if "evidence" in i or i.get("auto") == "ra_gate"}
    if item_id not in known:
        raise EvidenceError(f"Unknown go-live evidence item {item_id!r}")
    reference = (reference or "").strip()
    if not reference:
        raise EvidenceError("Say where the evidence lives (document id, register page or URL)")
    row = ComplianceEvidenceRecord(item_id=item_id, reference=reference[:500], valid_until=valid_until, recorded_by=user_id)
    session.add(row)
    return row

"""Security LOW follow-ups of #234 (shared-fundamentals writes):

* symbol squatting - an organisation's private company profile neither blocks nor shadows the shared one for the other
  organisations (a symbol is unique per scope: one shared profile, at most one per organisation);
* the shared financial periods have their own unique key (NULL tenant ids would otherwise never clash);
* the one-off repair of shared profiles a member wrote before the fix (`python -m app.fundamentals.repair_shared`):
  every member-written one quarantined in place whatever citation it carries, refilled (replaced, never merged) from the
  provider on request, never the operator's or the untouched provider's own, idempotent.
"""
import asyncio
import json
import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import AuditLogRecord, CompanyRecord, FinancialPeriodRecord, User
from app.fundamentals import ingest, repair_shared
from tests.test_auth_api import _register, _session_factory, client
from tests.test_fundamentals_api import _sample_profile
from tests.test_phase_ah_fundamentals_ingest import _mock_nse


def _org(tag):
    return {"Authorization": f"Bearer {_register(f'{tag}-{uuid.uuid4().hex[:8]}@example.com')}"}


def _sym(tag):
    return f"{tag}{uuid.uuid4().hex[:5].upper()}"


def _run(coro):
    return asyncio.run(coro)


def _with_provider(fn):
    ingest.set_provider_factory("nse", _mock_nse())
    try:
        return fn()
    finally:
        ingest.set_provider_factory("nse", None)
        ingest.set_provider_factory("nse", ingest._nse_factory)


def test_a_private_profile_neither_blocks_nor_shadows_the_shared_one():
    a, b, c = _org("squat-a"), _org("squat-b"), _org("squat-c")
    sym = _sym("SQ")
    # A squats the symbol with a private profile
    assert client.post("/api/fundamentals/companies", headers=a, json={**_sample_profile(sym), "name": "Squatted"}).status_code == 201
    # B is not blocked: it may enter its own (no hint that A has one) ...
    made = client.post("/api/fundamentals/companies", headers=b, json={**_sample_profile(sym), "name": "B's own"})
    assert made.status_code == 201, made.text
    assert client.get(f"/api/fundamentals/companies/{sym}", headers=b).json()["name"] == "B's own"
    # ... and C's refresh creates the shared profile, which every organisation without its own reads
    res = _with_provider(lambda: client.post("/api/fundamentals/refresh", headers=c, json={"symbols": [sym]}))
    assert res.status_code == 200 and res.json()["created"] == 1, res.text
    for who in (c, {}):
        assert client.get(f"/api/fundamentals/companies/{sym}", headers=who).json()["name"] == "Refreshed Co Ltd"
    assert client.get(f"/api/fundamentals/companies/{sym}", headers=a).json()["name"] == "Squatted"      # its own, for itself only
    listed = [x for x in client.get("/api/fundamentals/companies", headers=c).json() if x["symbol"] == sym]
    assert [x["name"] for x in listed] == ["Refreshed Co Ltd"]                                          # one row per symbol
    assert [x["name"] for x in client.get("/api/fundamentals/companies", headers=a).json() if x["symbol"] == sym] == ["Squatted"]
    # once the shared one exists a new private copy is refused (with the shared reason), as before
    late = client.post("/api/fundamentals/companies", headers=c, json=_sample_profile(sym))
    assert late.status_code == 409 and "shared reference data" in late.text
    again = client.post("/api/fundamentals/companies", headers=a, json=_sample_profile(sym))
    assert again.status_code == 409 and "use PUT" in again.text

    async def keys():
        async with _session_factory() as session:
            session.add(CompanyRecord(symbol=sym, name="dup", sector="x", industry="x"))      # a second shared profile
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()
            a_tenant = (await session.scalars(select(CompanyRecord.tenant_id).where(CompanyRecord.symbol == sym,
                                                                                    CompanyRecord.name == "Squatted"))).one()
            session.add(CompanyRecord(symbol=sym, name="dup", sector="x", industry="x", tenant_id=a_tenant))   # a second for A
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()
    _run(keys())


def test_shared_financial_periods_have_their_own_unique_key():
    sym = _sym("SP")

    async def go():
        async with _session_factory() as session:
            company = CompanyRecord(symbol=sym, name="Shared", sector="x", industry="x")
            session.add(company)
            await session.flush()

            def period():
                return FinancialPeriodRecord(company_id=company.id, period_type="ANNUAL", period_label="FY24",
                                             period_end_date=date(2024, 3, 31), revenue=1.0, ebitda=1.0, pat=1.0)
            session.add(period())
            await session.flush()
            session.add(period())                                   # the same shared period again
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()
    _run(go())


def _poison(sym, *, by_email=None, source=None, admin=False, hand=True):
    """A shared profile as a member could leave it before #234 (or a provider-made / operator one)."""
    async def go():
        async with _session_factory() as session:
            author = None
            if by_email is not None:
                author = await session.scalar(select(User).where(User.email == by_email))
                if admin:
                    author.role = "SUPER_ADMIN"
            extra = {"website": "https://evil.example", "business_description": "buy now", "business_segments_json": '["Fake"]'} if hand else {}
            row = CompanyRecord(symbol=sym, name="Poisoned Ltd", sector="Banking", industry="Banks", market_cap=1.0, isin="INE999X01999",
                                created_by=author.id if author else None, source_json=json.dumps({"source": source}) if source else None, **extra)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


NSE = "NSE India (nseindia.com/api/quote-equity)"


def _repair(mine, **kw):
    async def go():
        async with _session_factory() as session:
            found = await repair_shared.repair(session, **kw)
            await session.commit()
            return {f.symbol: f for f in found if f.symbol in mine}
    return _run(go())


def _assert_quarantined(sym):
    view = client.get(f"/api/fundamentals/companies/{sym}").json()
    assert view["name"] == sym and view["sector"] == "Unknown" and view["website"] is None and view["business_description"] is None
    assert view["market_cap"] is None and view["isin"] is None and view["business_segments"] == []


def test_the_repair_quarantines_every_member_written_shared_profile():
    member = f"repair-m-{uuid.uuid4().hex[:8]}@example.com"
    admin = f"repair-a-{uuid.uuid4().hex[:8]}@example.com"
    _register(member)
    _register(admin)
    poisoned, forged, edited, provider_made, operator_made, unknown = (_sym(t) for t in ("RP", "RF", "RE", "RV", "RO", "RU"))
    poisoned_id = _poison(poisoned, by_email=member)
    forged_id = _poison(forged, by_email=member, source=NSE)              # a member wrote the provider's citation itself
    edited_id = _poison(edited, source=NSE)                               # the provider's profile, a member's website on it
    _poison(provider_made, source=NSE, hand=False)                        # the provider's own, untouched
    _poison(operator_made, by_email=admin, admin=True)
    unknown_id = _poison(unknown)
    mine = {poisoned, forged, edited, provider_made, operator_made, unknown}

    dry = _repair(mine)
    assert set(dry) == {poisoned, forged, edited, unknown}
    assert all(dry[s].action == "none (dry run)" for s in (poisoned, forged, edited)) and dry[unknown].action.startswith("left")
    assert "citation" in dry[forged].reason and "website" in dry[edited].reason
    assert client.get(f"/api/fundamentals/companies/{poisoned}").json()["name"] == "Poisoned Ltd"     # a dry run changes nothing
    with pytest.raises(ValueError, match="no-refetch"):
        _repair(mine, apply=True)                                    # --apply alone: neither refetch nor an explicit no-refetch
    done = _repair(mine, apply=True, no_refetch=True)
    assert {s for s, f in done.items() if f.action == "quarantined (no refetch)"} == {poisoned, forged, edited}
    assert done[poisoned].author_tenant is not None and done[forged].author_tenant is not None
    for sym in (poisoned, forged, edited):
        _assert_quarantined(sym)
    assert client.get(f"/api/fundamentals/companies/{unknown}").json()["name"] == "Poisoned Ltd"     # not without the option
    for untouched in (provider_made, operator_made):
        assert client.get(f"/api/fundamentals/companies/{untouched}").json()["name"] == "Poisoned Ltd"
    assert not set(_repair(mine, apply=True, no_refetch=True)) & {poisoned, forged, edited}         # idempotent
    # the unattributed one on request, refilled from the provider
    refilled = _repair(mine, apply=True, include_unattributed=True, refetch=_mock_nse()())
    assert refilled[unknown].action == "replaced by the provider's profile"
    view = client.get(f"/api/fundamentals/companies/{unknown}").json()
    assert view["name"] == "Refreshed Co Ltd" and view["website"] is None and view["business_segments"] == []
    assert unknown not in _repair(mine, apply=True, no_refetch=True, include_unattributed=True)    # now provider-cited, no author

    async def audit():
        async with _session_factory() as session:
            rows = await session.scalars(select(AuditLogRecord.detail).where(AuditLogRecord.event == repair_shared.EVENT))
            return {json.loads(d)["company_id"] for d in rows}
    assert {poisoned_id, forged_id, edited_id, unknown_id} <= _run(audit())


def test_a_provider_profile_a_member_edited_is_replaced_not_merged_on_refetch():
    c = _org("repair-c")
    sym = _sym("RR")
    res = _with_provider(lambda: client.post("/api/fundamentals/refresh", headers=c, json={"symbols": [sym]}))
    assert res.status_code == 200 and res.json()["created"] == 1, res.text      # the shared profile: no author (the provider's)

    async def member_edits():                                                    # as a member could before #234
        async with _session_factory() as session:
            row = await session.scalar(select(CompanyRecord).where(CompanyRecord.symbol == sym, CompanyRecord.tenant_id.is_(None)))
            row.website, row.business_description, row.headquarters = "https://evil.example", "buy now", "Nowhere"
            row.business_segments_json = '["Fake"]'
            await session.commit()
    _run(member_edits())
    found = _repair({sym})
    assert sym in found and found[sym].reason.startswith("provider profile with member-written fields")   # cited, but edited
    done = _repair({sym}, apply=True, refetch=_mock_nse()())
    assert done[sym].action == "replaced by the provider's profile"
    view = client.get(f"/api/fundamentals/companies/{sym}", headers=c).json()
    assert view["name"] == "Refreshed Co Ltd" and view["isin"] == "INE000A01001"
    assert view["website"] is None and view["business_description"] is None and view["headquarters"] is None and view["business_segments"] == []
    assert sym not in _repair({sym})                                              # repaired: the provider's, nobody's


def test_the_repair_command_is_a_dry_run_by_default(monkeypatch, capsys):
    calls = {}

    async def fake_repair(session, *, apply=False, refetch=None, no_refetch=False, include_unattributed=False):
        calls.update(apply=apply, refetch=refetch, include_unattributed=include_unattributed)
        return []
    monkeypatch.setattr(repair_shared, "repair", fake_repair)
    monkeypatch.setattr("app.core.config.DATABASE_URL", "sqlite+aiosqlite:///:memory:")      # never opened: repair is faked
    assert asyncio.run(repair_shared._main([])) == 0
    assert calls == {"apply": False, "refetch": None, "include_unattributed": False}
    assert json.loads(capsys.readouterr().out)["applied"] is False


def test_the_fold_moves_every_table_that_hangs_off_a_company():
    """The refresh fold deletes the organisation's private profile after moving its rows onto the shared one: a table
    with a foreign key to `companies` that the fold does not move would be cascade-deleted with it (LG3's period
    versions, say). Fails when such a table is added without its COMPANY_CHILDREN (and, if unique per company, PERIOD_KEYED) entry."""
    import app.db.models  # noqa: F401 - registers every table
    from app.db.base import Base
    from app.fundamentals import persistence

    children = {t.name for t in Base.metadata.tables.values() if any(fk.column.table.name == "companies" for fk in t.foreign_keys)}
    assert children, "no table references companies?"
    moved = {m.__tablename__ for m in persistence.COMPANY_CHILDREN}
    assert children - moved == set(), f"tables hanging off companies that the fold would cascade-delete: {sorted(children - moved)}"
    assert moved <= children
    for name in children:
        table = Base.metadata.tables[name]
        assert "company_id" in table.c and "tenant_id" in table.c, f"{name}: the fold moves rows by company_id per tenant_id"
        keys = [[c.name for c in k.columns] for k in table.constraints if k.__class__.__name__ == "UniqueConstraint"]
        keys += [[c.name for c in i.columns] for i in table.indexes if i.unique]
        if any("company_id" in k for k in keys):         # moving a row could break its key: the fold must check it first
            assert name in {m.__tablename__ for m in persistence.PERIOD_KEYED}, f"{name}: unique per company, so fold_conflicts must check it"
            assert all({"period_type", "period_label"} <= set(k) for k in keys if "company_id" in k), f"{name}: fold_conflicts compares periods"


# --- data-safety follow-ups: no author on provider profiles, no blanking on a failed refetch, reversible repairs, the fold


def _audit(event):
    async def go():
        async with _session_factory() as session:
            return [(r.id, r.user_id, json.loads(r.detail)) for r in await session.scalars(select(AuditLogRecord).where(AuditLogRecord.event == event))]
    return _run(go())


def _shared_row(sym):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(CompanyRecord).where(CompanyRecord.symbol == sym, CompanyRecord.tenant_id.is_(None)))
    return _run(go())


def test_a_provider_made_shared_profile_has_no_author_and_is_not_a_suspect():
    a = _org("noauth-a")
    me = client.get("/api/auth/me", headers=a).json()
    created, promoted = _sym("NC"), _sym("NP")
    assert client.post("/api/fundamentals/companies", headers=a, json=_sample_profile(promoted)).status_code == 201   # A's own
    res = _with_provider(lambda: client.post("/api/fundamentals/refresh", headers=a, json={"symbols": [created, promoted]}))
    assert res.status_code == 200, res.text
    by_sym = {r["symbol"]: r for r in res.json()["results"]}
    assert by_sym[created]["created_company"] and "shared" in by_sym[promoted]["profile_changed"]
    for sym in (created, promoted):
        row = _shared_row(sym)
        assert row is not None and row.created_by is None                    # the provider's content: nobody's
    trail = {d["symbol"]: (uid, d) for _, uid, d in _audit(ingest.SHARED_PROFILE_EVENT) if d["symbol"] in (created, promoted)}
    assert trail[created][0] == me["id"] and trail[created][1]["how"] == "created"
    assert trail[promoted][1]["how"] == "promoted" and trail[promoted][1]["private_profile_author"] == me["id"]
    assert not set(_repair({created, promoted})) & {created, promoted}       # the suspect set does not grow


def test_apply_without_a_refetch_choice_is_refused_by_the_command(monkeypatch):
    monkeypatch.setattr("app.core.config.DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    for argv in (["--apply"], ["--apply", "--refetch", "--no-refetch"], ["--restore", "1", "--apply", "--refetch"]):
        with pytest.raises(SystemExit) as exc:
            asyncio.run(repair_shared._main(argv))
        assert exc.value.code == 2, argv


def test_a_failed_refetch_leaves_the_row_untouched_and_reports_it():
    member = f"repair-f-{uuid.uuid4().hex[:8]}@example.com"
    _register(member)
    sym = _sym("RX")
    company_id = _poison(sym, by_email=member)
    done = _repair({sym}, apply=True, refetch=_mock_nse(profile_status=403)())       # the provider blocks this IP
    assert done[sym].action.startswith("left untouched (provider:") and done[sym].audit_id is None
    view = client.get(f"/api/fundamentals/companies/{sym}").json()
    assert view["name"] == "Poisoned Ltd" and view["website"] == "https://evil.example"   # never blanked
    assert company_id not in {d["company_id"] for _, _, d in _audit(repair_shared.EVENT)}
    assert sym in _repair({sym})                                                          # still a suspect, for the next run


def test_a_repair_is_recorded_with_its_values_and_can_be_restored():
    member = f"repair-r-{uuid.uuid4().hex[:8]}@example.com"
    _register(member)
    sym, other = _sym("RS"), _sym("RT")
    _poison(sym, by_email=member)
    _poison(other, by_email=member)
    done = _repair({sym, other}, apply=True, no_refetch=True)
    _assert_quarantined(sym)
    audit_id = done[sym].audit_id
    detail = {i: d for i, _, d in _audit(repair_shared.EVENT)}[audit_id]
    assert detail["before"]["name"] == "Poisoned Ltd" and detail["before"]["website"] == "https://evil.example"
    assert detail["before"]["created_by"] == done[sym].created_by and detail["after"]["name"] == sym and detail["after"]["created_by"] is None

    async def restore(aid):
        async with _session_factory() as session:
            try:
                return await repair_shared.restore(session, aid)
            finally:
                await session.commit()
    restored = _run(restore(audit_id))
    assert restored["symbol"] == sym
    view = client.get(f"/api/fundamentals/companies/{sym}").json()
    assert view["name"] == "Poisoned Ltd" and view["website"] == "https://evil.example" and view["business_segments"] == ["Fake"]
    assert view["isin"] == "INE999X01999" and view["market_cap"] == 1.0
    assert _shared_row(sym).created_by is None                                       # the author is not put back
    assert any(d["audit_id"] == audit_id for _, _, d in _audit(repair_shared.RESTORE_EVENT))
    with pytest.raises(ValueError, match="changed since the repair"):
        _run(restore(audit_id))                                                      # already restored: the row is not what the repair left

    async def edit_then_restore():                                                   # something changed the row after its repair
        async with _session_factory() as session:
            row = await session.scalar(select(CompanyRecord).where(CompanyRecord.symbol == other, CompanyRecord.tenant_id.is_(None)))
            row.name = "Refreshed meanwhile"
            await session.commit()
    _run(edit_then_restore())
    with pytest.raises(ValueError, match="name"):
        _run(restore(done[other].audit_id))
    with pytest.raises(ValueError, match="not a shared-profile repair"):
        _run(restore(10 ** 9))


def test_the_bulk_refresh_folds_the_caller_s_own_profile_into_the_shared_one():
    a, c = _org("fold-a"), _org("fold-c")
    sym = _sym("FB")
    from tests.test_fundamentals_api import _sample_period
    assert client.post("/api/fundamentals/companies", headers=a, json={**_sample_profile(sym), "name": "A's own"}).status_code == 201
    assert client.post(f"/api/fundamentals/companies/{sym}/financials", headers=a, json=_sample_period("FY24", pat=7.0)).status_code == 201
    assert _with_provider(lambda: client.post("/api/fundamentals/refresh", headers=c, json={"symbols": [sym]})).json()["created"] == 1
    res = _with_provider(lambda: client.post("/api/fundamentals/refresh", headers=a, json={"symbols": [sym]}))
    assert res.status_code == 200, res.text
    summary = res.json()["results"][0]
    assert summary["profile_changed"][0] == "folded into shared" and not summary["errors"], summary   # not overwritten by the merge
    assert client.get(f"/api/fundamentals/companies/{sym}", headers=a).json()["name"] == "Refreshed Co Ltd"
    assert [p["pat"] for p in client.get(f"/api/fundamentals/companies/{sym}/financials", headers=a).json()] == [7.0]   # still A's
    assert client.get(f"/api/fundamentals/companies/{sym}/financials", headers=c).json() == []


def test_a_fold_that_would_clash_on_an_own_period_is_refused():
    a, c = _org("clash-a"), _org("clash-c")
    sym = _sym("FC")
    tenant = client.get("/api/auth/me", headers=a).json()["tenant_id"]
    from tests.test_fundamentals_api import _sample_period
    assert client.post("/api/fundamentals/companies", headers=a, json={**_sample_profile(sym), "name": "A's own"}).status_code == 201
    assert client.post(f"/api/fundamentals/companies/{sym}/financials", headers=a, json=_sample_period("FY24", pat=7.0)).status_code == 201
    assert _with_provider(lambda: client.post("/api/fundamentals/refresh", headers=c, json={"symbols": [sym]})).json()["created"] == 1

    async def own_period_on_shared():            # A already holds its own FY24 on the shared profile too
        async with _session_factory() as session:
            shared = await session.scalar(select(CompanyRecord).where(CompanyRecord.symbol == sym, CompanyRecord.tenant_id.is_(None)))
            session.add(FinancialPeriodRecord(company_id=shared.id, period_type="ANNUAL", period_label="FY24", period_end_date=date(2024, 3, 31),
                                              revenue=1.0, ebitda=1.0, pat=9.0, tenant_id=tenant))
            await session.commit()
    _run(own_period_on_shared())
    for call in (lambda: client.post("/api/fundamentals/refresh", headers=a, json={"symbols": [sym]}),
                 lambda: client.post(f"/api/fundamentals/companies/{sym}/refresh", headers=a, json={})):
        res = _with_provider(call)
        body = res.json()
        errors = body["results"][0]["errors"] if "results" in body else body["detail"]["errors"]
        assert any("already has these periods on the shared profile: ANNUAL FY24" in e for e in errors), body
    assert client.get(f"/api/fundamentals/companies/{sym}", headers=a).json()["name"] == "A's own"       # the private profile stays
    assert [p["pat"] for p in client.get(f"/api/fundamentals/companies/{sym}/financials", headers=a).json()] == [7.0]

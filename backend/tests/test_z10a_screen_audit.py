"""Z10-a: the one-off audit of stored screens and alert rules against today's validator (script + admin route)."""
import asyncio
import json

from app.db.models import AlertRuleRecord, ScreenRecord
from app.screener import nodes, parse
from app.screener.audit import audit_stored
from tests.test_admin_api import _admin, _owner
from tests.test_auth_api import _session_factory, client


def _ast(text: str) -> str:
    return json.dumps(nodes.to_json(parse(text)))


def test_audit_lists_stored_screens_and_rules_that_no_longer_validate():
    _, me = _owner("z10a-audit-owner@example.com")
    tenant = me["tenant_id"]

    async def seed():
        async with _session_factory() as session:
            ok = ScreenRecord(tenant_id=tenant, name="ok", source_text="close > SMA(close, 20)", ast_json=_ast("close > SMA(close, 20)"), base_tf="1d")
            bad = ScreenRecord(tenant_id=tenant, name="bad", source_text="Count(5, PatternBullish()) >= 2",
                               ast_json=_ast("Count(5, PatternBullish()) >= 2"), base_tf="1d")
            gone = ScreenRecord(tenant_id=tenant, name="archived", source_text="x", ast_json=_ast("Count(5, PatternBullish()) >= 2"),
                                base_tf="1d", archived=True)
            session.add_all([ok, bad, gone])
            await session.flush()
            session.add_all([
                AlertRuleRecord(tenant_id=tenant, name="uses bad", kind="screen", screen_id=bad.id, base_tf="1d"),
                AlertRuleRecord(tenant_id=tenant, name="k expr", kind="instrument", symbol="INFY",
                                condition_text="close > Supertrend(10, ATR(5) / 2)", base_tf="5m"),
                AlertRuleRecord(tenant_id=tenant, name="fine", kind="instrument", symbol="INFY", condition_text="close > VWAP()", base_tf="5m"),
            ])
            await session.commit()
            return bad.id, (await audit_stored(session))
    bad_id, report = asyncio.run(seed())
    mine = [r for r in report["failing"] if r["tenant_id"] == tenant]
    assert sorted((r["kind"], r["name"]) for r in mine) == [("alert_rule", "k expr"), ("screen", "bad")]
    screen = next(r for r in mine if r["kind"] == "screen")
    assert screen["id"] == bad_id and len(screen["alert_rules"]) == 1 and "last bar only" in screen["problems"][0]
    assert "not an expression" in next(r for r in mine if r["kind"] == "alert_rule")["problems"][0]

    headers, _ = _admin("z10a-audit-admin@example.com")
    body = client.get("/api/admin/screener/audit", headers=headers).json()
    assert {(r["kind"], r["name"]) for r in body["failing"] if r["tenant_id"] == tenant} == {("alert_rule", "k expr"), ("screen", "bad")}
    owner_headers, _ = _owner("z10a-audit-other@example.com")
    assert client.get("/api/admin/screener/audit", headers=owner_headers).status_code == 403

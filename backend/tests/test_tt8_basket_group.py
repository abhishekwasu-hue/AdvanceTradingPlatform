"""TT-8 (owner, 24 Oct): a manual basket's legs are one group, with "Exit this leg" and "Exit the whole group".
- the filled legs are stored as one group on existing columns (`group_meta` basket_group, `leg_role`), no `leg_group_id`
  (the monitor keeps judging each leg on its own stop / target);
- "Exit this leg" closes that leg only; "Exit the whole group" closes every leg, the sold legs first;
- a hedge exited before its sold leg: the warning without confirm (nothing closed), closed with confirm - never refused;
- both are exits (ADR-0004): the kill switch, a limit hit and approval-only never stop them;
- tenancy: another organisation 404, another member 403, no token 401."""
import asyncio
import json
import uuid

from sqlalchemy import select

from app.db.models import TradeRecord
from app.execution import approval
from app.execution import smart_execution as se
from app.paper import fno_groups
from tests.test_auth_api import _session_factory, client
from tests.test_fno2_basket import _condor_legs, _org_tenant, _post, condor  # noqa: F401 - the fixture
from tests.test_z3b_paper_account import _member
from tests.test_z5_p1d_risk_by_default import _flag


def _run(coro):
    return asyncio.run(coro)


def _trades(tenant_id: int) -> list:
    async def go():
        async with _session_factory() as session:
            return list(await session.scalars(select(TradeRecord).where(TradeRecord.tenant_id == tenant_id).order_by(TradeRecord.id)))
    return _run(go())


def _basket(condor, tag: str, *, smart: bool = False, max_open: int = 10):  # noqa: F811 - the fixture
    expiry, _, legs = condor
    t = _org_tenant(tag, max_open=max_open)
    if smart:
        _flag(se.FLAG, t["tenant_id"])
    ref = uuid.uuid4().hex
    out = _post(t, "execute", {"legs": _condor_legs(expiry, legs), "basket_ref": ref})
    assert out["placed"] == 4, out
    return t, ref, legs


def _by_symbol(tenant_id: int) -> dict:
    return {tr.symbol: tr for tr in _trades(tenant_id)}


def _exit(t, trade_id, what="exit-leg", body=None):
    return client.post(f"/api/paper/fno/positions/{trade_id}/{what}", headers=t["headers"], json=body)


def test_a_baskets_legs_form_one_group(condor):  # noqa: F811 - the fixture
    for smart in (False, True):
        t, ref, _ = _basket(condor, f"grp{int(smart)}", smart=smart)
        rows = _trades(t["tenant_id"])
        assert len(rows) == 4
        assert {json.loads(r.group_meta)[fno_groups.GROUP_KEY] for r in rows} == {ref}
        assert sorted(r.leg_role for r in rows) == ["LONG", "LONG", "SHORT", "SHORT"]
        assert all(r.leg_group_id is None for r in rows)            # each leg keeps its own stop / target in the monitor
        groups = client.get("/api/paper/fno/positions/groups", headers=t["headers"]).json()["groups"]
        assert len(groups) == 1 and groups[0]["kind"] == "basket" and sorted(groups[0]["trade_ids"]) == sorted(r.id for r in rows)
        assert sorted(groups[0]["hedge_ids"]) == sorted(r.id for r in rows if r.leg_role == "LONG")


def test_exit_this_leg_closes_that_leg_only(condor):  # noqa: F811 - the fixture
    t, _, legs = _basket(condor, "leg")
    short_put = _by_symbol(t["tenant_id"])[legs["short_put"][2]]
    out = _exit(t, short_put.id)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["status"] == "CLOSED" and [r["trade_id"] for r in body["legs"]] == [short_put.id] and body["legs"][0]["closed"]
    after = _by_symbol(t["tenant_id"])
    assert after[legs["short_put"][2]].exit_time is not None
    assert sum(1 for tr in after.values() if tr.exit_time is None) == 3
    assert "Exit this leg" in (after[legs["short_put"][2]].exit_reason or "")


def test_a_hedge_before_its_short_warns_without_confirm_and_closes_with_it(condor):  # noqa: F811 - the fixture
    t, _, legs = _basket(condor, "hedge")
    long_put = _by_symbol(t["tenant_id"])[legs["long_put"][2]]
    warned = _exit(t, long_put.id)
    assert warned.status_code == 200
    body = warned.json()
    assert body["status"] == "CONFIRM" and body["legs"] == [] and legs["short_put"][2] in body["warning"]
    assert "protects" in body["warning"] and "You can still exit it" in body["warning"]
    assert _by_symbol(t["tenant_id"])[legs["long_put"][2]].exit_time is None            # nothing closed yet
    done = _exit(t, long_put.id, body={"confirm": True})
    assert done.status_code == 200 and done.json()["status"] == "CLOSED"
    assert _by_symbol(t["tenant_id"])[legs["long_put"][2]].exit_time is not None
    # The short call's own hedge is not this one's business: the long call still warns about the short call only.
    long_call = _by_symbol(t["tenant_id"])[legs["long_call"][2]]
    other = _exit(t, long_call.id).json()
    assert other["status"] == "CONFIRM" and legs["short_call"][2] in other["warning"] and legs["short_put"][2] not in other["warning"]


def test_a_hedge_whose_short_is_closed_exits_without_a_warning(condor):  # noqa: F811 - the fixture
    t, _, legs = _basket(condor, "free")
    rows = _by_symbol(t["tenant_id"])
    assert _exit(t, rows[legs["short_put"][2]].id).json()["status"] == "CLOSED"
    out = _exit(t, rows[legs["long_put"][2]].id).json()
    assert out["status"] == "CLOSED" and out["warning"] is None


def test_exit_the_whole_group_closes_every_leg_shorts_first(condor):  # noqa: F811 - the fixture
    t, ref, legs = _basket(condor, "all")
    long_call = _by_symbol(t["tenant_id"])[legs["long_call"][2]]
    out = _exit(t, long_call.id, "exit-group")
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["status"] == "CLOSED" and body["group"] == ref and len(body["legs"]) == 4
    rows = _by_symbol(t["tenant_id"])
    order = [rows[r["symbol"]].leg_role for r in body["legs"]]
    assert order == ["SHORT", "SHORT", "LONG", "LONG"]                                     # sold legs first
    assert all(tr.exit_time is not None for tr in rows.values())


def test_exits_pass_with_the_kill_switch_on_limits_hit_and_approval_only(condor):  # noqa: F811 - the fixture
    t, _, legs = _basket(condor, "kill", max_open=4)          # at the open-positions limit
    _flag(approval.FLAG, t["tenant_id"])
    assert client.post("/api/kill-switch/tenant/engage", headers=t["headers"], json={"reason": "test"}).status_code == 200
    try:
        rows = _by_symbol(t["tenant_id"])
        leg = _exit(t, rows[legs["long_put"][2]].id, body={"confirm": True})
        assert leg.status_code == 200 and leg.json()["status"] == "CLOSED"
        group = _exit(t, rows[legs["short_call"][2]].id, "exit-group")
        assert group.status_code == 200 and group.json()["status"] == "CLOSED" and len(group.json()["legs"]) == 3
        assert all(tr.exit_time is not None for tr in _trades(t["tenant_id"]))
    finally:
        client.post("/api/kill-switch/tenant/disengage", headers=t["headers"])


def test_no_price_closes_nothing_in_the_group(condor, monkeypatch):  # noqa: F811 - the fixture
    t, _, legs = _basket(condor, "noprice")
    from tests.test_fno2_basket import _prices
    _prices(monkeypatch, {legs["short_put"][2]: 80.0})           # only one leg has a price now; no recent mark either
    out = _exit(t, _by_symbol(t["tenant_id"])[legs["short_put"][2]].id, "exit-group")
    assert out.status_code == 409 and "Nothing was closed" in out.json()["detail"]
    assert all(tr.exit_time is None for tr in _trades(t["tenant_id"]))


def test_leg_and_group_exits_are_the_callers_own(condor):  # noqa: F811 - the fixture
    t, _, legs = _basket(condor, "ten")
    trade_id = _by_symbol(t["tenant_id"])[legs["short_put"][2]].id
    other_org = _org_tenant("ten2")
    member = _member(t)
    for what in ("exit-leg", "exit-group"):
        assert _exit(other_org, trade_id, what).status_code == 404
        assert _exit(member, trade_id, what).status_code == 403
        assert client.post(f"/api/paper/fno/positions/{trade_id}/{what}").status_code == 401
    assert all(tr.exit_time is None for tr in _trades(t["tenant_id"]))
    # The other organisation's groups never show this basket.
    assert client.get("/api/paper/fno/positions/groups", headers=other_org["headers"]).json()["groups"] == []


def test_hedge_warning_is_pure_and_names_the_short():
    def tr(i, symbol, direction, kind="OPTION", exit_time=None):
        return TradeRecord(id=i, symbol=symbol, direction=direction, instrument_kind=kind, exit_time=exit_time)
    short_ce, long_ce, long_pe = tr(1, "NIFTY25000CE", "SHORT"), tr(2, "NIFTY25200CE", "LONG"), tr(3, "NIFTY24800PE", "LONG")
    legs = [short_ce, long_ce, long_pe]
    assert fno_groups.hedge_warning(short_ce, legs) is None                                 # a sold leg never warns
    assert fno_groups.hedge_warning(long_pe, legs) is None                                  # no open sold PE
    assert "NIFTY25000CE" in (fno_groups.hedge_warning(long_ce, legs) or "")
    spaced = [tr(5, "NIFTY 24900 CE 27 OCT 26", "SHORT"), tr(6, "NIFTY 24500 PE 27 OCT 26", "LONG"), tr(7, "RELIANCE 1500 CE 27 OCT 26", "LONG")]
    assert fno_groups.hedge_warning(spaced[1], spaced) is None                              # a PE never protects a sold CE
    assert "NIFTY 24900 CE" in (fno_groups.hedge_warning(spaced[2], spaced) or "")
    fut = tr(4, "NIFTYFUT", "LONG", kind="FUTURE")
    assert fno_groups.hedge_warning(fut, legs + [fut]) is None

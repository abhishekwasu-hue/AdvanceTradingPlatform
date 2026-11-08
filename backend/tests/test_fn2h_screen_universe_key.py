"""FN2-h: a screen alert rule can run on a screener list (`universe_key`) instead of 1-50 typed symbols."""
import json

from app.db.models import ScreenRecord
from app.screener import universes
from tests.test_auth_api import client
from tests.test_phase_l_ai import _owner
from tests.test_s4a_barclose import TUE, FakeFetch, _bars, _evaluate, _events, _flag, _rule, _run, _session_factory, ist


def _screen(headers) -> int:
    return int(client.post("/api/screener/screens", headers=headers, json={"name": "s", "source": "close > 100", "base_tf": "1d"}).json()["id"])


def _fake_list(monkeypatch, symbols, key="FNO_LIST"):
    async def symbols_of(session, k):
        return {"key": key, "name": "Test list", "symbols": list(symbols), "truncated": False, "source_count": len(symbols), "exchange": "NSE"} if k == key else None
    monkeypatch.setattr(universes, "symbols_of", symbols_of)


def test_api_create_with_universe_key_and_exactly_one_source(monkeypatch):
    headers, _ = _owner("fn2h-api@example.com")
    _fake_list(monkeypatch, ["TCS", "INFY"])
    _flag(True)
    try:
        sid = _screen(headers)
        base = {"name": "r", "kind": "screen", "screen_id": sid}
        made = client.post("/api/alerts/rules", headers=headers, json={**base, "universe_key": "FNO_LIST"})
        assert made.status_code == 201 and made.json()["universe_key"] == "FNO_LIST" and made.json()["symbols"] == []
        assert client.post("/api/alerts/rules", headers=headers, json=base).status_code == 422                                         # neither
        assert client.post("/api/alerts/rules", headers=headers, json={**base, "universe_key": "FNO_LIST", "symbols": ["TCS"]}).status_code == 422   # both
        assert client.post("/api/alerts/rules", headers=headers, json={**base, "universe_key": "NO_SUCH"}).status_code == 422
        old = client.post("/api/alerts/rules", headers=headers, json={**base, "symbols": ["tcs", "INFY"]})
        assert old.status_code == 201 and old.json()["symbols"] == ["TCS", "INFY"] and old.json()["universe_key"] is None
        listed = client.get("/api/alerts/rules", headers=headers).json()
        assert {r["universe_key"] for r in listed} == {"FNO_LIST", None}
    finally:
        _flag(False)


def _screen_row(tenant_id) -> int:
    async def go():
        async with _session_factory() as session:
            from app.screener import compile_screen, nodes
            ast, _ = compile_screen("close > 100", base_tf="5m")
            row = ScreenRecord(tenant_id=tenant_id, name="s", source_text="close > 100", ast_json=json.dumps(nodes.to_json(ast)), base_tf="5m", params_json="{}")
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def test_evaluation_resolves_the_list_and_fetches_in_batches(monkeypatch):
    _, me = _owner("fn2h-eval@example.com")
    names = [f"S{i}" for i in range(12)]
    _fake_list(monkeypatch, names)
    async def broker(session, tenant_id, user=None):
        return None
    monkeypatch.setattr(universes, "_broker_name", broker)
    monkeypatch.setattr(universes, "batch_plan", lambda b: {"size": 5, "per_minute": 0, "per_second": 1, "max_symbols": 500})
    sid = _screen_row(me["tenant_id"])
    rid = _rule(me["tenant_id"], kind="screen", symbol=None, condition=None, screen_id=sid, universe_key="FNO_LIST")
    seen = []

    class Recording(FakeFetch):
        async def __call__(self, session, tenant_id, symbols, exchange, base_tf, lookback, **kw):
            seen.append(list(symbols))
            return await super().__call__(session, tenant_id, symbols, exchange, base_tf, lookback)
    fetch = Recording({n: _bars(ist(TUE, 9, 15), [99, 105]) for n in names})
    assert _evaluate(rid, ist(TUE, 9, 26), fetch)[0] == ("done", 12)
    assert [len(p) for p in seen] == [5, 5, 2] and [s for p in seen for s in p] == names
    assert len(_events(rid)) == 12


def test_symbols_rule_is_fetched_in_one_call_as_before(monkeypatch):
    _, me = _owner("fn2h-old@example.com")
    sid = _screen_row(me["tenant_id"])
    rid = _rule(me["tenant_id"], kind="screen", symbol=None, condition=None, screen_id=sid, universe_json=json.dumps(["TCS", "INFY", "WIPRO"]))
    fetch = FakeFetch({"TCS": _bars(ist(TUE, 9, 15), [99, 105])})
    assert _evaluate(rid, ist(TUE, 9, 26), fetch)[0] == ("done", 1) and fetch.calls == 1


def test_a_missing_list_is_a_plain_problem(monkeypatch):
    _, me = _owner("fn2h-gone@example.com")
    _fake_list(monkeypatch, [], key="OTHER")
    sid = _screen_row(me["tenant_id"])
    rid = _rule(me["tenant_id"], kind="screen", symbol=None, condition=None, screen_id=sid, universe_key="GONE")
    state, problem = _evaluate(rid, ist(TUE, 9, 26), FakeFetch({}))
    assert state == ("error", 0) and "list is gone" in problem

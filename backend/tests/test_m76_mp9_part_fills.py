"""M76 MP-9 (E-2): per-leg part fills in the workspace and the re-price helper for a leg not filled.

The helper adds NO order path: it shows the leg's new last price and, on his confirm, sends his existing answer
"Complete the rest" (app/execution/incomplete_structures.py) - recorded by the route, sent by the trading worker through
every entry refusal and the engine at the price read then (hedges first), PAPER and LIVE through the same functions.
Proven here:
- the view gives each leg still to place its exchange (for its new last price), additively;
- a double tap records ONE answer (the second says "Already answered"), and the worker sends the rest ONCE (one more
  order row, idempotency key `<group key>:L<leg>:R<round>`), PAPER and LIVE alike;
- the first answer wins: after "Keep them" a late "Complete the rest" tap sends nothing (nothing is done twice).
"""
import pytest
from sqlalchemy import func, select

from app.db.models import OrderRecord
from app.execution import incomplete_structures as inc
from tests.test_auth_api import _session_factory, client
from tests.test_se7_live_multileg import _complete, _execute, _incomplete_paper, _no_poll_wait, _RejectingBroker, _run, _smart  # noqa: F401
from tests.test_trading_worker import _tenant, _trades

pytestmark = pytest.mark.usefixtures("no_open_positions_from_earlier_tests", "owner_switched_live_on")


def _orders(tenant_id) -> int:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(OrderRecord).where(OrderRecord.tenant_id == tenant_id))
    return _run(go())


def _double_tap(t, group) -> None:
    url = f"/api/trading/incomplete/{group}/answer"
    first = client.post(url, headers=t["headers"], json={"action": "complete_rest"})
    second = client.post(url, headers=t["headers"], json={"action": "complete_rest"})
    assert first.status_code == second.status_code == 200
    assert first.json()["state"] == inc.COMPLETING
    assert second.json()["state"] == inc.COMPLETING and "Already answered" in second.json()["message"]


def test_the_view_gives_each_pending_leg_its_exchange(monkeypatch):
    t = _smart(_tenant("mp9-view@example.com"))
    _incomplete_paper(t, monkeypatch, key="mp9v")
    item = client.get("/api/trading/incomplete", headers=t["headers"]).json()["items"][0]
    assert [(p["exchange"], p["side"], p["role"]) for p in item["pending"]] == [("NFO", "SELL", "sold")]
    assert set(item["pending"][0]) == {"symbol", "side", "role", "quantity", "exchange"}
    assert item["filled"] and all(set(f) == {"trade_id", "symbol", "side", "role", "quantity", "entry_price", "open"} for f in item["filled"])


def test_paper_double_tap_sends_the_rest_once(monkeypatch):
    t = _smart(_tenant("mp9-paper@example.com"))
    result = _incomplete_paper(t, monkeypatch, key="mp9p")
    before = _orders(t["tenant_id"])
    _double_tap(t, result.leg_group_id)
    assert [o["state"] for o in _complete(t)] == [inc.COMPLETED]
    assert _complete(t) == []                                                  # a second worker cycle: nothing to send
    assert _orders(t["tenant_id"]) == before + 1 and len(_trades(t["tenant_id"])) == 2


def test_live_double_tap_sends_the_rest_once_through_the_same_functions(monkeypatch):
    t = _smart(_tenant("mp9-live@example.com"))
    broker = _RejectingBroker("24500 PE")
    result = _execute(t, mode="LIVE", key="mp9l", broker=broker)
    assert result.state == "INCOMPLETE"
    sent = len(broker.placed)
    _double_tap(t, result.leg_group_id)
    broker.reject = "no-such-symbol"
    assert [o["state"] for o in _complete(t, broker=broker)] == [inc.COMPLETED]
    assert _complete(t, broker=broker) == []
    assert len(broker.placed) == sent + 1 and broker.placed[-1].intent == "ENTRY"   # one more order, an entry


def test_the_first_answer_wins_and_an_exit_choice_is_never_done_twice(monkeypatch):
    t = _smart(_tenant("mp9-first@example.com"))
    result = _incomplete_paper(t, monkeypatch, key="mp9f")
    url = f"/api/trading/incomplete/{result.leg_group_id}/answer"
    keep = client.post(url, headers=t["headers"], json={"action": "keep"})
    assert keep.json()["state"] == inc.KEPT
    late = client.post(url, headers=t["headers"], json={"action": "complete_rest"})
    assert late.status_code == 200 and "Already answered" in late.json()["message"]
    assert _complete(t) == [] and len(_trades(t["tenant_id"])) == 1            # nothing sent after Keep them

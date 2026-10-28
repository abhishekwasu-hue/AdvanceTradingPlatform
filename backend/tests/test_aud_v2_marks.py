"""AUD-V2 (owner audit 28 Oct; PX-1 P1): open P&L after hours from the last stored mark.

With no live price (market closed, no broker session) a position is valued at the mark the position monitor stored while
its market was open, labelled with that time; never at the entry price passed off as real."""
from datetime import datetime, timezone

from app.db.models import TradeRecord
from tests.def0_legacy import legacy
from tests.test_auth_api import _session_factory, client
from tests.test_phase_m_closure import _owner, _run, _seed_trade

MARKED_AT = datetime(2026, 10, 28, 9, 59, tzinfo=timezone.utc)      # 15:29 IST


def _mark(trade_id: int, price: float, when: datetime) -> None:
    async def go():
        async with _session_factory() as session:
            t = await session.get(TradeRecord, trade_id)
            t.mark_price, t.mark_time = price, when
            await session.commit()
    _run(go())


def test_after_hours_the_open_pnl_is_the_last_mark_with_its_time():
    headers, me = _owner("aud-v2@example.com")
    assert client.put("/api/risk-settings", headers=headers, json=legacy().model_dump()).status_code == 200
    first = _seed_trade(me["tenant_id"], me["id"], symbol="RELIANCE", entry=100.0, qty=300)
    _seed_trade(me["tenant_id"], me["id"], symbol="TCS", entry=50.0, qty=10)              # never marked: stays at entry
    _mark(first, 104.0, MARKED_AT)
    body = client.get("/api/portfolio/exposure", headers=headers).json()
    assert body["price_source"] == "mark" and body["priced_symbols"] == ["RELIANCE"]
    assert body["marks"] == {"RELIANCE": MARKED_AT.isoformat()}
    row = next(r for r in body["by_symbol"] if r["symbol"] == "RELIANCE")
    assert row["unrealised_pnl"] == 1_200.0                                                # (104 - 100) x 300
    # Asked without prices: the entry valuation, as before.
    plain = client.get("/api/portfolio/exposure?live_prices=false", headers=headers).json()
    assert plain["price_source"] == "entry" and plain["marks"] == {}

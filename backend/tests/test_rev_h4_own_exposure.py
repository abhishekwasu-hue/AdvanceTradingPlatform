"""REV-H4 (docs/design/REV1_PLAN.md M1): the Terminal shows the caller's own positions (`/positions?mine=true`), so its
exposure read must have the same scope. Two members of one organisation hold the same symbol: with `mine=true` each sees
only his own exposure and P&L; without it the answer is the organisation's, exactly as before."""
from datetime import datetime, timezone

from app.db.models import TradeRecord
from tests.test_auth_api import _session_factory, client
from tests.test_terminal_t1 import _member, _run, _trade, _who

MARKED_AT = datetime(2026, 10, 28, 9, 59, tzinfo=timezone.utc)      # 15:29 IST


def _mark(trade_id: int, price: float) -> None:
    async def go():
        async with _session_factory() as session:
            t = await session.get(TradeRecord, trade_id)
            t.mark_price, t.mark_time = price, MARKED_AT
            await session.commit()
    _run(go())


def _exposure(who: dict, **params) -> dict:
    res = client.get("/api/portfolio/exposure", headers=who["headers"], params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _row(body: dict, symbol: str) -> dict:
    return next(r for r in body["by_symbol"] if r["symbol"] == symbol)


def test_two_members_holding_one_symbol_each_see_only_their_own_exposure_and_pnl():
    owner = _who("h4")
    member = _member(owner)
    _mark(_trade(owner, symbol="H4SYM", entry_price=100.0, quantity=10.0), 110.0)
    _mark(_trade(member, symbol="H4SYM", entry_price=100.0, quantity=30.0), 110.0)

    mine = _exposure(owner, mine="true")
    assert mine["open_positions"] == 1
    assert _row(mine, "H4SYM")["notional"] == 1_100.0 and _row(mine, "H4SYM")["unrealised_pnl"] == 100.0
    assert mine["unrealised_pnl"] == 100.0
    assert mine["prices"] == {"H4SYM": 110.0}             # the price itself: never a notional over a quantity of another scope

    theirs = _exposure(member, mine="true")
    assert theirs["open_positions"] == 1
    assert _row(theirs, "H4SYM")["notional"] == 3_300.0 and theirs["unrealised_pnl"] == 300.0
    assert theirs["prices"] == {"H4SYM": 110.0}

    # Without `mine`: the organisation's book, as before.
    org = _exposure(owner)
    assert org["open_positions"] == 2 and _row(org, "H4SYM")["notional"] == 4_400.0 and org["unrealised_pnl"] == 400.0


def test_mine_leaves_out_another_members_symbol_and_realised_today():
    owner = _who("h4b")
    member = _member(owner)
    _trade(owner, symbol="H4OWN")
    _trade(member, symbol="H4TEAM")
    _trade(member, symbol="H4TEAM", exit_time=datetime.now(timezone.utc), exit_price=105.0, pnl=50.0)
    mine = _exposure(owner, mine="true", live_prices="false")
    assert {r["symbol"] for r in mine["by_symbol"]} == {"H4OWN"} and mine["realised_today"] == 0.0
    org = _exposure(owner, live_prices="false")
    assert {r["symbol"] for r in org["by_symbol"]} == {"H4OWN", "H4TEAM"} and org["realised_today"] == 50.0

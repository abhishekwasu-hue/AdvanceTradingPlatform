"""P1 (owner bug 27 Oct): the chain showed NIFTY 27 Oct prices while every ticket said "not in the instrument list yet ...
try again later" - the ticket resolves from our instrument master, and one failed daily sync left the whole day empty.

Covered here: the sync is retried with a back-off (and caught up after a worker start); the state is stored and shown;
a ticket miss starts one rate-limited refresh and says what is happening; on an expiry day the chain opens the next expiry
unless the user turned that off; an exit never depends on the master (ADR-0004).
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.db.models import InstrumentRecord, PlatformControlRecord
from app.instruments import master_sync
from app.instruments.master_sync import refresh_on_demand as REAL_REFRESH  # imported before the conftest stub
from app.market_data.calendar import IST
from app.option_chain import chain_view
from app.paper import fno_ticket
from tests.test_auth_api import _session_factory, client
from tests.test_fno2_basket import _org_tenant, condor  # noqa: F401 - the fixture is used by name

run = asyncio.run
MORNING = datetime(2026, 10, 27, 8, 30, tzinfo=IST)


def _reset():
    async def go():
        async with _session_factory() as session:
            row = await session.get(PlatformControlRecord, master_sync.STATE_KEY)
            if row is not None:
                await session.delete(row)
                await session.commit()
    run(go())


def test_due_retries_with_a_back_off_and_stops_once_today_synced():
    assert not master_sync.due({}, MORNING.replace(hour=7), hour=8)                     # before the sync hour
    assert master_sync.due({}, MORNING, hour=8)                                          # never synced: due now
    failed = {"last_attempt_at": MORNING.isoformat(), "last_error": "ConnectError: no network"}
    assert not master_sync.due(failed, MORNING + timedelta(minutes=5), hour=8)          # inside the back-off
    assert master_sync.due(failed, MORNING + timedelta(minutes=master_sync.RETRY_MINUTES), hour=8)
    assert master_sync.due(failed, MORNING + timedelta(hours=3), hour=8)                 # also during market hours
    done = {"last_ok_at": MORNING.isoformat(), "last_attempt_at": MORNING.isoformat()}
    assert not master_sync.due(done, MORNING + timedelta(hours=5), hour=8)
    assert master_sync.due(done, MORNING + timedelta(days=1), hour=8)                    # tomorrow: a new day


def test_run_records_a_failure_and_a_success():
    _reset()

    async def failing(session, exchanges):
        raise ConnectionError("upstox master download timed out")

    async def ok(session, exchanges):
        return {"NSE": 3000, "NFO": 90000}

    async def go():
        async with _session_factory() as session:
            bad = await master_sync.run(session, ["NSE"], MORNING, sync=failing)
        async with _session_factory() as session:
            stored = await master_sync.read_state(session)
        async with _session_factory() as session:
            good = await master_sync.run(session, ["NSE"], MORNING + timedelta(minutes=12), sync=ok)
        return bad, stored, good
    bad, stored, good = run(go())
    assert "timed out" in bad["last_error"] and bad["last_ok_at"] is None
    assert stored["last_error"] == bad["last_error"]
    assert good["last_error"] is None and good["rows"] == 93000 and master_sync.synced_today(good, MORNING + timedelta(hours=1))


def test_on_demand_refresh_is_rate_limited_and_says_what_happens(monkeypatch):
    _reset()
    calls = []

    async def fake_sync(session, exchanges):
        calls.append(tuple(exchanges))
        return {"NSE": 1, "NFO": 2}
    monkeypatch.setattr("app.instruments.master.sync_upstox", fake_sync)

    async def go():
        first = await REAL_REFRESH(_session_factory, ["NSE"])
        await asyncio.sleep(0.05)
        second = await REAL_REFRESH(_session_factory, ["NSE"])
        await asyncio.sleep(0)
        return first, second
    first, second = run(go())
    assert "being refreshed now" in first and "try again later" not in first.lower()
    assert calls == [("NSE",)]                              # the second ask inside ON_DEMAND_MINUTES starts nothing
    assert "contract list" in second


def test_status_text_never_says_try_again_later():
    failing = {"last_ok_at": datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc).isoformat(), "last_error": "HTTP 503"}
    text = master_sync.status_text(failing, refreshing=False)
    assert "could not be updated" in text and "26 Oct 08:30 IST" in text and "later" not in text


def test_a_ticket_miss_explains_the_refresh():
    with pytest.raises(fno_ticket.ContractNotFound) as err:
        async def go():
            async with _session_factory() as session:
                await fno_ticket.find_contract(session, "NIFTY", date(2031, 1, 2), 12345.0, "CE")
        run(go())
    message = str(err.value)
    assert "not in ATP's contract list yet" in message and "being refreshed now" in message
    assert "try again later" not in message.lower()


def test_expiry_day_opens_the_next_expiry_unless_turned_off():
    today = date(2026, 10, 27)
    listed = [today, date(2026, 11, 3), date(2026, 11, 10)]
    assert chain_view.default_expiry(listed, today) == today                       # M68 PR-4 (TS1-044): Not set = the nearest
    assert chain_view.default_expiry(listed, today, next_on_expiry_day=True) == date(2026, 11, 3)
    assert chain_view.default_expiry(listed, today, next_on_expiry_day=False) == today
    assert chain_view.default_expiry(listed, date(2026, 10, 28), next_on_expiry_day=True) == date(2026, 11, 3)


def test_the_setting_is_read_from_the_users_option_chain_prefs():
    t = _org_tenant(f"p1exp-{uuid.uuid4().hex[:4]}")
    user = type("U", (), {"tenant_id": t["tenant_id"], "id": t["user_id"]})()

    async def read():
        async with _session_factory() as session:
            return await chain_view.next_on_expiry_day(session, user)
    assert run(read()) is True                                                          # default ON
    saved = client.put("/api/me/prefs/option_chain", headers=t["headers"], json={"expiry_day_next": False})
    assert saved.status_code == 200, saved.text
    assert run(read()) is False


def test_an_exit_never_needs_the_instrument_master(condor):  # noqa: F811
    expiry, base, legs = condor
    t = _org_tenant(f"p1exit-{uuid.uuid4().hex[:4]}")
    lp = legs["long_put"]
    body = {"underlying": "NIFTY", "expiry": expiry.isoformat(), "strike": lp[0], "right": "PE", "side": "BUY", "lots": 1,
            "order_type": "MARKET", "product": "NRML", "chain_ltp": lp[3], "client_ref": uuid.uuid4().hex}
    placed = client.post("/api/paper/fno/order", headers=t["headers"], json=body)
    assert placed.status_code == 200 and placed.json()["status"] == "PLACED", placed.text

    async def wipe():                                       # the contract disappears from the master (a failed sync)
        async with _session_factory() as session:
            await session.execute(delete(InstrumentRecord).where(InstrumentRecord.tradingsymbol == lp[2]))
            await session.commit()
    run(wipe())
    out = client.post(f"/api/positions/{placed.json()['trade_id']}/exit-now", headers=t["headers"])
    assert out.status_code == 200, out.text


def test_an_upstox_expiry_at_midnight_ist_keeps_its_own_date():
    """normalise_expiry read epoch values as UTC: an expiry stamped 00:00 IST (18:30 UTC the day before) was stored one
    day early, so the ticket's exact-expiry lookup found no contract on the expiry day."""
    from app.instruments.master import normalise_expiry
    for hour, minute in ((0, 0), (9, 15), (15, 30), (23, 59)):
        stamp = int(datetime(2026, 10, 27, hour, minute, tzinfo=IST).timestamp() * 1000)
        assert normalise_expiry(stamp) == date(2026, 10, 27), (hour, minute)

"""AUD-T2: the expiry-day and margin checklist (GET /api/fno/expiry-checklist, flags ui_v2 + expiry_checklist).

Covered with a frozen clock: 404 while the flag is off; the session before expiry vs the expiry day vs any other day; a
holiday moves the session before expiry; stock (delivery, broker rule with its link) vs index (cash) settlement; in /
out of the money from the live price; no price -> a plain sentence; the next expiry and the same-strike basket leg from
the instrument master; the caller's own positions only; no advice wording."""
import asyncio
import re
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import update

from app.db.models import BrokerCredentialRecord, InstrumentRecord, TradeRecord
from app.expiry_checklist import brokers, build
from app.expiry_checklist import routes as checklist_routes
from app.market_data.calendar import IST
from tests.test_auth_api import _session_factory, client
from tests.test_settings_a_connect_ai import _flags
from tests.test_ui2_4a1_prefs import _invite, _who

URL = "/api/fno/expiry-checklist"
EXP = date(2033, 6, 14)          # a Tuesday
NEXT = date(2033, 6, 21)
T_MINUS_1 = date(2033, 6, 13)    # Monday
FRIDAY = date(2033, 6, 10)
STOCK = "AUDTWOSTK"
ADVICE = re.compile(r"\b(buy|sell|target|stop|should|must|recommend)\w*\b", re.I)


def _run(coro):
    return asyncio.run(coro)


def _at(day: date, hh: int = 11) -> datetime:
    return datetime(day.year, day.month, day.day, hh, 0, tzinfo=IST).astimezone(timezone.utc)


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(list(rows))
            await session.commit()
    _run(go())


def _trade(who, symbol, underlying, *, kind="OPTION", expiry=EXP, qty=50.0, direction="SHORT"):
    return TradeRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], mode="PAPER", symbol=symbol, strategy_id="manual",
                       direction=direction, entry_time=_at(date(2033, 6, 1)), entry_price=100.0, quantity=qty, stop_loss=0.0,
                       charges=0.0, instrument_kind=kind, expiry=expiry, underlying_symbol=underlying, exchange="NFO")


_seeded = False


def _seed_master():
    global _seeded
    if _seeded:
        return
    _seeded = True
    rows = []
    for und, k, lot in ((STOCK, 1000.0, 25), ("FINNIFTY", 25000.0, 65)):
        for e in (EXP, NEXT):
            for right in ("CE", "PE"):
                rows.append(InstrumentRecord(broker="upstox", exchange="NFO", instrument_key=f"upstox|t2{und}{e:%y%m%d}{int(k)}{right}",
                                             tradingsymbol=f"{und}{e:%y%b}{int(k)}{right}".upper(), underlying=und, instrument_type=right,
                                             expiry=e, strike=k, lot_size=lot))
        for e in (EXP, NEXT):
            rows.append(InstrumentRecord(broker="upstox", exchange="NFO", instrument_key=f"upstox|t2{und}{e:%y%m%d}FUT",
                                         tradingsymbol=f"{und}{e:%y%b}FUT".upper(), underlying=und, instrument_type="FUT",
                                         expiry=e, lot_size=lot))
    _add(*rows)


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    _seed_master()
    _flags(ui_v2=True, expiry_checklist=True)
    state = {"holidays": set(), "now": _at(T_MINUS_1), "prices": {"NIFTY FIN SERVICE": 25300.0, STOCK: 990.0}}

    async def holidays(*_a, **_k):
        return set(state["holidays"])

    async def lookup_for(_session, _user):
        async def lookup(symbol, exchange="NSE"):
            if symbol not in state["prices"]:
                raise LookupError("no price")
            return state["prices"][symbol]
        return lookup
    monkeypatch.setattr(build, "family_holidays", holidays)
    monkeypatch.setattr(build, "_spot_lookup", lookup_for)
    monkeypatch.setattr(checklist_routes, "_now", lambda: state["now"])
    yield state
    _flags(ui_v2=False, expiry_checklist=False)

    async def close_mine():
        async with _session_factory() as session:
            await session.execute(update(TradeRecord).where(TradeRecord.strategy_id == "manual", TradeRecord.expiry.in_((EXP, NEXT)),
                                                            TradeRecord.exit_time.is_(None)).values(
                exit_time=datetime.now(timezone.utc), exit_price=TradeRecord.entry_price, pnl=0.0, exit_reason="test cleanup"))
            await session.commit()
    _run(close_mine())


def _get(who):
    r = client.get(URL, headers=who["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _texts(body):
    out = list(body["notes"]) + [b["note"] for b in body["brokers"]]
    for row in body["rows"]:
        out += [row["headline"], row["money_text"] or "", row["settlement_text"], row["next_text"]]
    return out


def test_flag_off_is_404(_setup):
    who = _who("t2-off")
    _flags(expiry_checklist=False)
    r = client.get(URL, headers=who["headers"])
    assert r.status_code == 404 and r.headers.get("X-Feature-Disabled") == "expiry_checklist"
    _flags(expiry_checklist=True, ui_v2=False)
    assert client.get(URL, headers=who["headers"]).status_code == 404
    assert client.get(URL).status_code == 401


def test_session_before_expiry_vs_expiry_day_vs_other_days(_setup):
    who = _who("t2-days")
    _add(_trade(who, "FINNIFTY 25000 CE 14 JUN 33", "NIFTY FIN SERVICE"))
    rows = _get(who)["rows"]
    assert [r["when"] for r in rows] == ["next_session"]
    assert "expires next session, Tue 14 Jun 2033" in rows[0]["headline"]
    _setup["now"] = _at(EXP)
    rows = _get(who)["rows"]
    assert [r["when"] for r in rows] == ["today"] and "expires today" in rows[0]["headline"]
    for other in (FRIDAY, date(2033, 6, 8), date(2033, 6, 15)):
        _setup["now"] = _at(other)
        assert _get(who)["rows"] == []


def test_a_holiday_moves_the_session_before_expiry(_setup):
    who = _who("t2-holiday")
    _add(_trade(who, "FINNIFTY 25000 PE 14 JUN 33", "FINNIFTY"))
    _setup["holidays"] = {T_MINUS_1}
    _setup["now"] = _at(FRIDAY)
    assert [r["when"] for r in _get(who)["rows"]] == ["next_session"]
    assert build.when_due(EXP, T_MINUS_1, {T_MINUS_1}) == "next_session"   # the weekend / holiday itself still points at it
    assert build.when_due(EXP, FRIDAY, set()) is None


def test_index_is_cash_and_stock_is_delivery_with_the_brokers_own_link(_setup):
    who = _who("t2-settle")
    _add(_trade(who, "FINNIFTY 25000 CE 14 JUN 33", "NIFTY FIN SERVICE"),
         _trade(who, f"{STOCK} 1000 PE 14 JUN 33", STOCK),
         BrokerCredentialRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], broker_name="zerodha", encrypted_payload="x"))
    body = _get(who)
    by = {r["underlying"]: r for r in body["rows"]}
    assert by["FINNIFTY"]["settlement"] == "cash" and "settles in cash" in by["FINNIFTY"]["settlement_text"]
    assert by[STOCK]["settlement"] == "delivery" and "delivery of shares" in by[STOCK]["settlement_text"]
    assert [b["broker"] for b in body["brokers"]] == ["zerodha"]
    assert body["brokers"][0]["url"] == brokers.BROKER_HELP["zerodha"].url and body["brokers"][0]["url"].startswith("https://support.zerodha.com/")


def test_index_only_shows_no_broker_rule_and_unknown_broker_shows_all(_setup):
    who = _who("t2-index-only")
    _add(_trade(who, "FINNIFTY 25000 CE 14 JUN 33", "FINNIFTY"))
    assert _get(who)["brokers"] == []
    assert [b["broker"] for b in brokers.notes_for(["fyers"])] == sorted(brokers.BROKER_HELP)
    assert {"zerodha", "upstox", "dhan", "angel_one"} <= set(brokers.BROKER_HELP)
    assert all(b.url.startswith("https://") for b in brokers.BROKER_HELP.values())


def test_in_and_out_of_the_money_from_the_live_price(_setup):
    who = _who("t2-itm")
    _add(_trade(who, "FINNIFTY 25000 CE 14 JUN 33", "FINNIFTY"), _trade(who, "FINNIFTY 25000 PE 14 JUN 33", "FINNIFTY"),
         _trade(who, f"{STOCK}33JUN1000PE", STOCK))
    by = {(r["underlying"], r["right"]): r for r in _get(who)["rows"]}
    assert by[("FINNIFTY", "CE")]["moneyness"] == "in" and "In the money now" in by[("FINNIFTY", "CE")]["money_text"]
    assert by[("FINNIFTY", "PE")]["moneyness"] == "out" and "Out of the money now" in by[("FINNIFTY", "PE")]["money_text"]
    assert by[(STOCK, "PE")]["moneyness"] == "in"             # 990 below the 1000 put strike
    assert build.moneyness("CE", 100.0, 100.0) == "at"


def test_no_price_is_said_plainly(_setup):
    who = _who("t2-noprice")
    _setup["prices"] = {}
    _add(_trade(who, "FINNIFTY 25000 CE 14 JUN 33", "FINNIFTY"))
    row = _get(who)["rows"][0]
    assert row["moneyness"] is None and row["priced"] is False
    assert row["money_text"] == "No live price for FINNIFTY now, so in or out of the money is not known."


def test_next_expiry_and_the_same_strike_basket_leg(_setup):
    who = _who("t2-roll")
    _add(_trade(who, "FINNIFTY 25000 CE 14 JUN 33", "FINNIFTY", qty=130.0),
         _trade(who, f"{STOCK}33JUNFUT", STOCK, kind="FUTURE", qty=25.0, direction="LONG"))
    by = {r["right"]: r for r in _get(who)["rows"]}
    ce = by["CE"]
    assert ce["next_expiry"] == NEXT.isoformat() and ce["next_text"] == "Next expiry: Tue 21 Jun 2033."
    assert ce["basket_leg"] == {"underlying": "FINNIFTY", "expiry": NEXT.isoformat(), "strike": 25000.0, "right": "CE",
                                "side": "SELL", "lots": 2, "label": "FINNIFTY 21 JUN 25000 CE"}
    fut = by["FUT"]
    assert fut["money_text"] is None and fut["settlement"] == "delivery"
    assert fut["basket_leg"]["right"] == "FUT" and fut["basket_leg"]["strike"] == 0.0 and fut["basket_leg"]["side"] == "BUY"


def test_only_the_callers_own_positions(_setup):
    owner = _who("t2-scope")
    member = _invite(owner, "USER")
    stranger = _who("t2-stranger")
    _add(_trade(owner, "FINNIFTY 25000 CE 14 JUN 33", "FINNIFTY"), _trade(member, "FINNIFTY 25000 PE 14 JUN 33", "FINNIFTY"))
    assert [r["right"] for r in _get(owner)["rows"]] == ["CE"]
    assert [r["right"] for r in _get(member)["rows"]] == ["PE"]
    assert _get(stranger)["rows"] == []


def test_no_advice_wording_anywhere(_setup):
    who = _who(f"t2-words-{uuid.uuid4().hex[:4]}")
    _add(_trade(who, "FINNIFTY 25000 CE 14 JUN 33", "FINNIFTY"), _trade(who, f"{STOCK} 1000 CE 14 JUN 33", STOCK),
         _trade(who, f"{STOCK}33JUNFUT", STOCK, kind="FUTURE"))
    body = _get(who)
    texts = _texts(body) + [b.note for b in brokers.BROKER_HELP.values()]
    assert len(body["rows"]) == 3
    for text in texts:
        assert not ADVICE.search(text), text


def test_the_strike_comes_from_the_instrument_master_when_it_holds_the_contract(_setup):
    """A compact symbol runs the date digits into the strike; the master's own strike is used, never the text guess."""
    who = _who("t2-master-strike")
    compact = f"FINNIFTY{EXP:%y}O0125000CE"                       # the text parser would read 125000
    _add(InstrumentRecord(broker="upstox", exchange="NFO", instrument_key=f"upstox|t2compact{EXP:%y%m%d}", tradingsymbol=compact,
                          underlying="FINNIFTY", instrument_type="CE", expiry=EXP, strike=25000.0, lot_size=65),
         _trade(who, compact, "FINNIFTY"))
    row = next(r for r in _get(who)["rows"] if r["symbol"] == compact)
    assert row["moneyness"] == "in"                               # 25300 above the 25000 call; 125000 would read "out"

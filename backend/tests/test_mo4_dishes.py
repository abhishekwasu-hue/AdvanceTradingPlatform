"""Master Order 4 slice 4 (app/growth/dishes.py): the "ready-made dish" on Signals, My positions, the Journal, Charts and
Find setups, behind `ui_v2` + `growth_v2` (OFF).

Covered: both flags off = 404 naming the flag, an unknown page 404, Charts without a symbol 422; each dish from fixtures
(his own proposals, signals, PAPER trades, journal, watchlists, scan rules and matches, and the shared daily history) and
"nothing" said in plain words without data (never a sample figure); the language: no buy / sell / target / stop wording
or advice in any line, no number in a setup line; no line counts up or praises trading more; another member's rows never
appear; the pure rules (the paper record, the result at the invalidation, premium kept, best / costliest setups, the
chart story, scan groups).
"""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.chart_analyze.signal_language import call_words
from app.db.models import AiActionRecord, AlertEventRecord, AlertRuleRecord, MdCandleRecord, SignalHistoryRecord, TradeRecord, User, WatchlistRecord
from app.growth import dishes
from app.market_data.calendar import IST
from app.market_lake.bhavcopy_backfill import bar_end
from app.rhythm import words
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _register, _session_factory, client

run = asyncio.run
UTC = timezone.utc
# A Thursday far from every other suite's dates.
DAY = date(2033, 5, 12)
NOW = datetime(2033, 5, 12, 14, 0, tzinfo=IST).astimezone(UTC)


def _ist(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST).astimezone(UTC)


def _who(email: str):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me


def _on(me, *flags):
    for name in flags:
        _set_flag(name, me["tenant_id"], on=True)


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(list(rows))
            await session.commit()
    run(go())


def _dish(me, page, **kw):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            return await dishes.build(session, user, page, now=NOW, **kw)
    return run(go())


def _lines(d):
    return [line for s in d["sections"] for line in s["lines"]]


def _assert_language(d):
    for s in d["sections"]:
        for text in s["lines"] + ([s["empty"]] if s["empty"] else []) + [s["title"]]:
            assert call_words(text) == [], text
            assert words.clean(text) == text, text
    assert call_words(d["note"]) == []
    if d["headline"]:
        assert words.clean(d["headline"]) == d["headline"]


def _trade(me, symbol, *, entry=100.0, stop=95.0, mark=None, qty=10, direction="LONG", at=None, exit_at=None, pnl=None,
           strategy="pullback_custom", **extra):
    return TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol=symbol, strategy_id=strategy,
                       direction=direction, entry_time=at or _ist(DAY, 9, 30), entry_price=entry, quantity=qty, stop_loss=stop,
                       mark_price=mark, mark_time=_ist(DAY, 13) if mark is not None else None, exit_time=exit_at,
                       exit_price=None if exit_at is None else entry, pnl=pnl, charges=0.0, **extra)


def _proposal(me, symbol, at, *, owner=None, status="PROPOSED", reasons=("Pullback reached a buyer area",)):
    ev = {"symbol": symbol, "direction": "LONG", "proposal_price": 101.25, "stop_loss": 98.5, "target1": 107.0, "source": "scan",
          "signal": {"strategy_name": "Pullback", "reasons": list(reasons)}}
    return AiActionRecord(tenant_id=me["tenant_id"], action="ENTRY", rule="scan_setup", reason="setup", evidence_json=json.dumps(ev),
                          status=status, expires_at=at + timedelta(hours=8), created_at=at, owner_user_id=owner or me["id"])


# --- flags and routes -----------------------------------------------------------------------------------------------

def test_flags_off_means_404_naming_the_flag_and_bad_requests():
    headers, me = _who("mo4d-flags@example.com")
    got = client.get("/api/growth/dish/signals", headers=headers)
    assert got.status_code == 404 and got.headers.get("X-Feature-Disabled") == "ui_v2"
    _on(me, "ui_v2")
    got = client.get("/api/growth/dish/signals", headers=headers)
    assert got.status_code == 404 and got.headers.get("X-Feature-Disabled") == "growth_v2"
    _on(me, "growth_v2")
    for page in ("signals", "positions", "journal", "screener"):
        got = client.get(f"/api/growth/dish/{page}", headers=headers)
        assert got.status_code == 200, (page, got.text)
        assert got.json()["page"] == page
    assert client.get("/api/growth/dish/charts?symbol=RELIANCE", headers=headers).status_code == 200
    assert client.get("/api/growth/dish/charts", headers=headers).status_code == 422
    assert client.get("/api/growth/dish/orders", headers=headers).status_code == 404
    assert client.get("/api/growth/dish/signals").status_code in (401, 403)


# --- signals --------------------------------------------------------------------------------------------------------

def test_signals_dish_from_his_own_inbox_and_paper_record():
    _, me = _who("mo4d-signals@example.com")
    _, other = _who("mo4d-signals-other@example.com")
    _add(_proposal(me, "MO4SIGA", _ist(DAY, 10)), _proposal(me, "MO4SIGB", _ist(DAY, 11), reasons=("RSI 41.2 below 50",)),
         _proposal(other, "MO4OTHER", _ist(DAY, 11)),
         SignalHistoryRecord(tenant_id=me["tenant_id"], user_id=me["id"], strategy_id="pullback_custom", symbol="MO4SIGC", direction="LONG",
                             signal_time=_ist(DAY, 12), entry=101.5, stop_loss=99.0, target1=106.0, risk_reward=1.8, score=80,
                             grade="High Quality", reasons_json="[]", timeframe_combo="15m", created_at=_ist(DAY, 12)),
         *[_trade(me, f"MO4R{i}", at=_ist(DAY - timedelta(days=10 + i), 10), exit_at=_ist(DAY - timedelta(days=10 + i), 14),
                  pnl=(250.0 if i < 3 else -120.0)) for i in range(5)],
         _trade(me, "MO4OLD", at=_ist(DAY - timedelta(days=200), 10), exit_at=_ist(DAY - timedelta(days=200), 14), pnl=999.0))
    d = _dish(me, "signals")
    _assert_language(d)
    by = {s["id"]: s for s in d["sections"]}
    waiting = by["waiting"]["lines"]
    assert waiting[0] == "2 signals wait for your decision."
    assert d["headline"] == waiting[0]
    assert any(line.startswith("MO4SIGA: matched your scan \"Pullback\"") and "pullback reached a buyer area" in line for line in waiting)
    assert any(line.startswith("MO4SIGB") for line in waiting)
    assert not any("41" in line for line in waiting)                         # a "why" line with a value is left out
    assert not any("MO4OTHER" in line for line in _lines(d))                  # another member's proposal never appears
    assert by["today"]["lines"] == ["2 from your scans and 1 from the signal generator."]
    record = by["record"]["lines"]
    assert len(record) == 1 and "5 closed paper trades in the last 90 days; 3 ended with a gain and 2 did not." in record[0]
    # UX-11b C1: the same facts typed for the page's table and chips
    assert by["waiting"]["kind"] == "waiting" and by["waiting"]["rows"][0] == {"type": "waiting", "count": 2}
    assert {r["symbol"] for r in by["waiting"]["rows"][1:]} == {"MO4SIGA", "MO4SIGB"}
    assert by["today"]["rows"][0]["counts"] == {"scan": 2, "library": 1}
    assert [(r["closed"], r["won"], r["lost"], r["days"]) for r in by["record"]["rows"]] == [(5, 3, 2, 90)]


def test_signals_dish_with_nothing_says_so():
    _, me = _who("mo4d-signals-empty@example.com")
    d = _dish(me, "signals")
    assert d["headline"] == "Nothing waits for your decision right now."
    assert _lines(d) == []
    assert all(s["empty"] for s in d["sections"])
    _assert_language(d)


def test_strategy_record_never_counts_up_trading():
    unit = SimpleNamespace
    units = [unit(setup="a", closed=True, pnl=10.0)] * 2 + [unit(setup="b", closed=True, pnl=-5.0), unit(setup="b", closed=False, pnl=None)]
    lines = dishes.strategy_record_lines(units)
    assert lines == ["a: 2 closed paper trades in the last 90 days; 2 ended with a gain and 0 did not.",
                     "b: 1 closed paper trade in the last 90 days; 0 ended with a gain and 1 did not."]
    for line in lines:
        assert not any(w in line.lower() for w in ("great", "well done", "keep trading", "more trades", "streak"))


# --- positions ------------------------------------------------------------------------------------------------------

def test_positions_dish_risk_in_plain_words():
    from app.db.models import RiskSettingsRecord
    _, me = _who("mo4d-positions@example.com")
    _add(RiskSettingsRecord(tenant_id=me["tenant_id"], capital=100000.0, max_daily_loss_pct=2.0),
         _trade(me, "MO4PA", entry=100.0, stop=95.0, mark=96.0, qty=10),                      # -50 at its invalidation, near it
         _trade(me, "MO4PB", entry=200.0, stop=210.0, mark=190.0, qty=5, direction="SHORT"),   # -50 at its invalidation
         _trade(me, "MO4PC", entry=50.0, stop=0.0, mark=None, qty=10))                         # no invalidation, no price yet
    d = _dish(me, "positions")
    _assert_language(d)
    by = {s["id"]: s for s in d["sections"]}
    risk = by["risk"]["lines"]
    assert risk[0] == "Open P&L across 3 positions: +₹10 at the last prices (some prices are not in yet)."
    assert "If each position reached the invalidation you set for it, the result from entry would be about -₹100 in total." in risk
    assert "That is 5% of your own daily loss limit." in risk
    assert "1 position has no invalidation level set." in risk
    assert by["near"]["lines"] == ["MO4PA (long): close to the invalidation you set for it."]
    assert by["risk"]["rows"] == [{"type": "risk", "positions": 3, "open_pnl": 10.0, "prices_missing": True, "at_invalidation": -100.0,
                                   "of_loss_limit_pct": 5, "no_invalidation": 1}]
    assert by["near"]["rows"] == [{"type": "position", "symbol": "MO4PA", "side": "LONG", "pnl": -40.0, "near": True}]
    assert "premium" not in by                                                 # nothing written: the section is left out
    assert by["ahead"]["lines"] == []


def test_positions_dish_premium_kept_and_expiry_ahead():
    _, me = _who("mo4d-premium@example.com")
    exp = DAY + timedelta(days=4)
    _add(_trade(me, "MO4NIFTY25000CE", entry=120.0, stop=180.0, mark=45.0, qty=50, direction="SHORT", instrument_kind="OPTION",
                underlying_symbol="MO4IDX", expiry=exp),
         _trade(me, "MO4NIFTY24000PE", entry=80.0, stop=40.0, mark=None, qty=50, direction="LONG", instrument_kind="OPTION",
                underlying_symbol="MO4IDX", expiry=exp + timedelta(days=30)))
    d = _dish(me, "positions")
    _assert_language(d)
    by = {s["id"]: s for s in d["sections"]}
    assert by["premium"]["lines"] == ["MO4NIFTY25000CE (written): 62% of the premium kept so far (+₹3,750 of ₹6,000)."]
    assert by["ahead"]["lines"] == [f"{exp.strftime('%a %d %b')}: your MO4IDX contracts expire (1 position)."]
    assert by["premium"]["rows"] == [{"type": "premium", "label": "MO4NIFTY25000CE (written)", "kept_pct": 62.0, "kept": 3750.0,
                                      "received": 6000.0}]
    assert by["ahead"]["rows"] == [{"type": "expiry", "date": exp.isoformat(), "underlying": "MO4IDX", "count": 1}]


def test_positions_dish_without_positions():
    _, me = _who("mo4d-positions-empty@example.com")
    d = _dish(me, "positions")
    assert d["headline"] == "No open paper positions."
    assert [s["id"] for s in d["sections"]] == ["risk"] and d["sections"][0]["empty"]


def test_result_at_invalidation_and_premium_rules():
    t = SimpleNamespace
    long_ = t(direction="LONG", stop_loss=95.0, entry_price=100.0, quantity=10, leg_group_id=None)
    trailed = t(direction="LONG", stop_loss=104.0, entry_price=100.0, quantity=10, leg_group_id=None)
    none = t(direction="LONG", stop_loss=0.0, entry_price=100.0, quantity=10, leg_group_id=None)
    assert dishes.at_invalidation([long_]) == -50.0
    assert dishes.at_invalidation([trailed]) == 40.0
    assert dishes.at_invalidation([none]) is None
    sold = t(instrument_kind="OPTION", direction="SHORT", entry_price=100.0, quantity=10, mark_price=130.0)
    assert dishes.premium_kept([sold]) == {"received": 1000.0, "kept": -300.0, "pct": -30}
    bought = t(instrument_kind="OPTION", direction="LONG", entry_price=100.0, quantity=10, mark_price=130.0)
    assert dishes.premium_kept([bought]) is None
    stock = t(instrument_kind="UNDERLYING", direction="SHORT", entry_price=100.0, quantity=10, mark_price=90.0)
    assert dishes.premium_kept([stock]) is None


# --- journal --------------------------------------------------------------------------------------------------------

def test_journal_dish_needs_the_journal():
    _, me = _who("mo4d-journal-off@example.com")
    d = _dish(me, "journal")
    assert d["available"] is False and d["sections"] == []


def test_journal_dish_score_setups_and_habits():
    _, me = _who("mo4d-journal@example.com")
    _on(me, "discipline_engine")
    monday = DAY - timedelta(days=DAY.weekday())
    _add(_trade(me, "MO4JA", at=_ist(monday, 10), exit_at=_ist(monday, 14), pnl=300.0, strategy="pullback_custom"),
         _trade(me, "MO4JB", at=_ist(monday + timedelta(days=1), 10), exit_at=_ist(monday + timedelta(days=1), 14), pnl=-200.0,
                strategy="breakout_custom", notes="Took it on the retest."),
         _trade(me, "MO4JC", at=_ist(monday - timedelta(days=7), 10), exit_at=_ist(monday - timedelta(days=7), 14), pnl=-150.0,
                strategy="breakout_custom"))
    d = _dish(me, "journal")
    _assert_language(d)
    by = {s["id"]: s for s in d["sections"]}
    score = by["score"]["lines"]
    assert score and score[0].startswith("Discipline score this week: ") and score[0].endswith(" out of 100 (0 open · 2 closed).")   # AUD-V4: what the score covers
    assert "It counts how closely you followed your own plan, not how many trades you took." in score
    setups = by["setups"]["lines"]
    assert setups[0].startswith("Best: ") and "+₹300 over 1 closed trade" in setups[0]
    assert setups[1].startswith("Cost the most: ") and "-₹350 over 2 closed trades" in setups[1]
    assert "1 of this week's 2 closed trades has no note of yours yet." in by["habits"]["lines"]
    assert by["score"]["rows"][0]["type"] == "discipline" and isinstance(by["score"]["rows"][0]["score"], int)
    best, worst = by["setups"]["rows"]
    assert (best["type"], best["net"], best["closed"]) == ("best", 300.0, 1)
    assert (worst["type"], worst["net"], worst["closed"]) == ("worst", -350.0, 2)
    assert {"type": "no_note", "count": 1, "closed": 2} in by["habits"]["rows"]


def test_setup_extremes_rules():
    u = SimpleNamespace
    best, worst = dishes.setup_extremes([u(setup="a", closed=True, pnl=5.0), u(setup="b", closed=True, pnl=-1.0),
                                         u(setup="b", closed=True, pnl=-2.0), u(setup="c", closed=False, pnl=None)])
    assert best == {"setup": "a", "net": 5.0, "closed": 1} and worst == {"setup": "b", "net": -3.0, "closed": 2}
    assert dishes.setup_extremes([u(setup="a", closed=True, pnl=0.0)]) == (None, None)


def test_behaviour_words_pass_the_guard():
    from app.journal.review import TAG_LABELS
    assert set(dishes.BEHAVIOUR_WORDS) == set(TAG_LABELS)
    for label in dishes.BEHAVIOUR_WORDS.values():
        assert words.clean(label) == label


# --- charts ---------------------------------------------------------------------------------------------------------

def _zigzag(n_legs=14):
    """An up-structure: legs of five rising bars and two falling ones, each swing higher than the one before."""
    closes, price = [], 100.0
    for _ in range(n_legs):
        for _ in range(5):
            price += 2.0
            closes.append(price)
        for _ in range(2):
            price -= 3.0
            closes.append(price)
    return closes


def _seed(key, closes, end: date):
    days, d = [], end
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()

    async def go():
        async with _session_factory() as session:
            for day, c in zip(days, closes):
                session.add(MdCandleRecord(instrument_key=key, timeframe="day", ts=bar_end(day), source="test-mo4d", version=1,
                                           open=c, high=c + 1.0, low=c - 1.0, close=c, volume=1))
            await session.commit()
    run(go())


def test_charts_dish_story_and_his_own_on_the_symbol():
    _, me = _who("mo4d-charts@example.com")
    _seed("NSE:MO4CHART", _zigzag(), DAY - timedelta(days=1))
    _add(_trade(me, "MO4CHART", entry=150.0, stop=140.0, mark=160.0, qty=2),
         _proposal(me, "MO4CHART", _ist(DAY, 10)),
         WatchlistRecord(tenant_id=me["tenant_id"], user_id=me["id"], name="Swing ideas", symbols_json=json.dumps(["MO4CHART", "MO4X"])))
    d = _dish(me, "charts", symbol="mo4chart")
    _assert_language(d)
    by = {s["id"]: s for s in d["sections"]}
    story = by["story"]["lines"]
    assert story[0].startswith("Daily swings: higher highs and higher lows")
    assert any(line.startswith("Weekly swings: ") for line in story)
    assert any(line.startswith("Read from the close of ") for line in story)             # yesterday's close, said so
    assert d["headline"] == "Daily swings: higher highs and higher lows."
    assert not any(ch.isdigit() for ch in story[0])                                         # no level in the story
    yours = by["yours"]["lines"]
    assert yours[0] == "You hold 1 open paper position here, open P&L +₹20."
    assert any(line.startswith("Your latest setup here, today: matched your scan") for line in yours)
    assert "On your lists: Swing ideas." in yours
    assert by["yours"]["title"] == "Yours on MO4CHART"
    story_row = by["story"]["rows"][0]
    assert story_row["daily"] == "UP" and story_row["weekly"] in ("UP", "DOWN", "SIDEWAYS")
    assert story_row["as_of"] == (DAY - timedelta(days=1)).isoformat()
    assert story_row["place"] in ("ABOVE_HIGH", "BELOW_LOW", "BETWEEN")
    held, setup, lists = by["yours"]["rows"]
    assert held == {"type": "held", "count": 1, "open_pnl": 20.0, "near": False}
    assert setup["symbol"] == "MO4CHART" and setup["today"] is True and lists == {"type": "lists", "names": ["Swing ideas"]}


def test_charts_dish_without_history_says_so():
    _, me = _who("mo4d-charts-empty@example.com")
    d = _dish(me, "charts", symbol="MO4NOBARS")
    by = {s["id"]: s for s in d["sections"]}
    assert by["story"]["lines"] == [] and "No stored daily history for MO4NOBARS" in by["story"]["empty"]
    assert d["headline"] is None and by["yours"]["lines"] == []
    d = _dish(me, "charts", symbol="MO4NOBARS", exchange="MCX")
    assert "NSE's stored daily history" in {s["id"]: s for s in d["sections"]}["story"]["empty"]


def test_where_close_and_lake_key():
    s = SimpleNamespace
    swings = [s(kind="LOW", price=90.0), s(kind="HIGH", price=110.0)]
    assert dishes.where_close(120.0, swings) == "the last close is above the last swing high"
    assert dishes.where_close(80.0, swings) == "the last close is below the last swing low"
    assert dishes.where_close(100.0, swings) == "the last close is between the last swing low and the last swing high"
    assert dishes.where_close(100.0, swings[:1]) is None
    assert dishes.lake_key("reliance") == "NSE:RELIANCE"
    assert dishes.lake_key("NIFTY 50").startswith("NSE_INDEX:")


# --- find setups ----------------------------------------------------------------------------------------------------

def test_screener_dish_groups_his_scan_matches():
    _, me = _who("mo4d-screener@example.com")
    _, other = _who("mo4d-screener-other@example.com")
    _on(me, "screener_v2")
    _on(other, "screener_v2")

    def rule(owner, name):
        return AlertRuleRecord(tenant_id=owner["tenant_id"], created_by=owner["id"], name=name, kind="screen", status="active")
    mine, mine2, theirs = rule(me, "Pullback to demand"), rule(me, "Range edges"), rule(other, "Not his")
    _add(mine, mine2, theirs,
         WatchlistRecord(tenant_id=me["tenant_id"], user_id=me["id"], name="Core", symbols_json=json.dumps(["MO4S2"])))

    def event(r, owner, symbol, minute):
        return AlertEventRecord(tenant_id=owner["tenant_id"], rule_id=r.id, symbol=symbol, condition_hash="h", bar_time=_ist(DAY, 10),
                                idem_key=f"mo4d-{r.id}-{symbol}-{minute}", priority="normal", values_json=json.dumps({"rsi": 41.2}),
                                created_at=_ist(DAY, 10, minute))
    _add(event(mine, me, "MO4S1", 1), event(mine, me, "MO4S2", 2), event(mine, me, "MO4S3", 3), event(mine2, me, "MO4S4", 4),
         event(theirs, other, "MO4THEIRS", 5),
         event(mine, me, "MO4YDAY", 0).__class__(tenant_id=me["tenant_id"], rule_id=mine.id, symbol="MO4YDAY", condition_hash="h",
                                                 bar_time=_ist(DAY - timedelta(days=1), 10), idem_key="mo4d-yday", priority="normal",
                                                 values_json="{}", created_at=_ist(DAY - timedelta(days=1), 10)))
    d = _dish(me, "screener")
    _assert_language(d)
    by = {s["id"]: s for s in d["sections"]}
    today = by["today"]["lines"]
    assert today[0] == "\"Pullback to demand\": 3 matches - MO4S3, MO4S2, MO4S1."
    assert today[1] == "\"Range edges\": 1 match - MO4S4."
    assert "On your own lists: MO4S2." in today
    assert not any("MO4THEIRS" in line or "MO4YDAY" in line for line in _lines(d))
    assert d["headline"] == "4 symbols matched your scans today."
    assert by["scans"]["lines"] == ["2 scans of yours run on their own; new matches show here and on Signals."]
    assert by["today"]["rows"][0] == {"type": "scan", "name": "Pullback to demand", "count": 3, "symbols": ["MO4S3", "MO4S2", "MO4S1"]}
    assert {"type": "on_lists", "symbols": ["MO4S2"]} in by["today"]["rows"]
    assert by["scans"]["rows"] == [{"type": "active", "count": 2}]


def test_screener_dish_without_scans():
    _, me = _who("mo4d-screener-empty@example.com")
    d = _dish(me, "screener")
    assert d["headline"] is None and _lines(d) == []
    assert "None of your scans runs on its own yet." in {s["id"]: s for s in d["sections"]}["scans"]["empty"]


def test_match_groups_rule():
    items = [{"source": {"kind": "scan", "name": "B"}, "symbol": "x"}, {"source": {"kind": "scan", "name": "A"}, "symbol": "y"},
             {"source": {"kind": "scan", "name": "A"}, "symbol": "Y"}, {"source": {"kind": "autopilot", "name": "A"}, "symbol": "z"},
             {"source": {"kind": "scan", "name": "Buy the dip"}, "symbol": "w"}]
    assert dishes.match_groups(items) == [("A", ["Y"]), ("B", ["X"]), ("Your scan", ["W"])]

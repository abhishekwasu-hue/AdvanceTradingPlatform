"""DATA-H (owner, 24 Oct): the index history the chart reader and the Copilot read.

No network: the broker is a fake adapter serving START-labelled Daily and 1-minute bars and recording every call; the
clock is fixed. Dates are in 2023 (no holiday calendar in the test database, so every weekday is a trading day).
Covered: DH1 backfill into the owner's PRIVATE store only (never the shared lake, never another session), idempotent on a
re-run, resumable after a stop and after a refused rate token; DH2 the daily pass and `--if-missing`; DH3 the closing
auction flags and that the chart layer, the zones job and the context panel read the flagged bars without them; DH4 the
health lines (MISSING / SHORT); DH5 the honest wording of the Copilot and the market line; DH6 the official last close
with its date and source; the tenancy of every read (z10c); the migration.
"""
import asyncio
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import func, select

from app.ai import copilot_chat as chat
from app.ai.tools import ToolResult
from app.ai.tools import copilot as copilot_tools
from app.core import config
from app.core.models import OHLCVBar
from app.db.models import BrokerCredentialRecord, LakeBackfillWindowRecord, MdBarFlagRecord, MdCandleRecord, MdTenantCandleRecord, Tenant, User
from app.market_data.api_budget import BudgetUsed
from app.market_data.calendar import IST
from app.market_lake import index_history as dh
from app.market_lake import own_bars
from tests.test_auth_api import _register, _session_factory

FRI = date(2023, 3, 10)
THU = date(2023, 3, 9)
NOW = datetime(2023, 3, 10, 17, 0, tzinfo=IST)          # after the close: Friday's bars are final
W = LakeBackfillWindowRecord


def _run(coro):
    return asyncio.run(coro)


def _user(email: str) -> User:
    _register(email)

    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(User).where(User.email == email))
    return _run(go())


def _tenant_name(tenant_id: int) -> str:
    async def go():
        async with _session_factory() as session:
            return (await session.get(Tenant, tenant_id)).name
    return _run(go())


def _credential(user: User, *, tenant_id: Optional[int] = None) -> int:
    async def go():
        async with _session_factory() as session:
            rec = BrokerCredentialRecord(tenant_id=tenant_id or user.tenant_id, user_id=user.id, broker_name="upstox",
                                         account_label="primary", encrypted_payload="not-used-by-the-fake-adapter", token_status="VALID",
                                         token_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
            session.add(rec)
            await session.commit()
            return rec.id
    return _run(go())


def _teammate(owner: User, email: str) -> User:
    from app.auth.security import hash_password

    async def go():
        async with _session_factory() as session:
            user = User(tenant_id=owner.tenant_id, email=email, hashed_password=hash_password("S3cur3Pass!"), role="USER", is_active=True)
            session.add(user)
            await session.commit()
            return user
    return _run(go())


def _count(model, *where) -> int:
    async def go():
        async with _session_factory() as session:
            return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)
    return _run(go())


def minute_bars(day: date, base: float, *, after_close: int = 0) -> List[OHLCVBar]:
    """A full session of START-labelled 1-minute bars (09:15 .. 15:29 IST) plus `after_close` flat bars from 15:30."""
    start = datetime.combine(day, time(9, 15), tzinfo=IST)
    out = []
    for i in range(375):
        p = base + (i % 50) * 0.5
        out.append(OHLCVBar(timestamp=start + timedelta(minutes=i), open=p, high=p + 1, low=p - 1, close=p + 0.25, volume=0))
    last = out[-1].close
    for j in range(after_close):
        out.append(OHLCVBar(timestamp=datetime.combine(day, time(15, 30), tzinfo=IST) + timedelta(minutes=j), open=last, high=last,
                            low=last, close=last, volume=0))
    return out


def daily_bars(first: date, last: date, base: float) -> List[OHLCVBar]:
    out, d, i = [], first, 0
    while d <= last:
        if d.weekday() < 5:
            p = base + (i % 40) * 10
            out.append(OHLCVBar(timestamp=datetime.combine(d, time(0, 0), tzinfo=IST), open=p, high=p + 30, low=p - 30, close=p + 5))
            i += 1
        d += timedelta(days=1)
    return out


class FakeBroker:
    """Daily and 1-minute bars per broker symbol; the historical endpoint has no "today" (like Upstox)."""

    def __init__(self, daily: Dict[str, List[OHLCVBar]], minutes: Dict[str, List[OHLCVBar]], *, fail: Optional[Dict[int, BaseException]] = None):
        self.daily, self.minutes, self.fail = daily, minutes, fail or {}
        self.calls: List[tuple] = []

    async def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        self.calls.append(("historical", symbol, exchange, interval, from_date.date(), to_date.date()))
        exc = self.fail.get(len(self.calls))
        if exc is not None:
            raise exc
        lo, hi = from_date.date(), to_date.date()
        src = self.daily if interval == "day" else self.minutes
        return [b for b in src.get(symbol, []) if lo <= b.timestamp.astimezone(IST).date() <= hi
                and (interval == "day" or b.timestamp.astimezone(IST).date() != FRI)]

    async def get_intraday_candles(self, symbol, exchange, interval):
        self.calls.append(("intraday", symbol, exchange, interval))
        return [b for b in self.minutes.get(symbol, []) if b.timestamp.astimezone(IST).date() == FRI]

    async def place_order(self, *a, **k):
        raise AssertionError("the backfill placed an order")


class Owner:
    def __init__(self, email: str, monkeypatch, *, indices=("NIFTY",), credential: bool = True) -> None:
        self.user = _user(email)
        self.tenant_id = self.user.tenant_id
        self.credential_id = _credential(self.user) if credential else None
        monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_ID", self.tenant_id)
        monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_NAME", _tenant_name(self.tenant_id))
        monkeypatch.setattr(config, "HISTORY_OWNER_USER_ID", None)
        monkeypatch.setattr(config, "LAKE_BACKFILL_INDICES", list(indices))
        monkeypatch.setattr(config, "LAKE_BACKFILL_DAILY_YEARS", 1)
        monkeypatch.setattr(config, "LAKE_BACKFILL_INTRADAY_DAYS", 45)
        self.used: List[int] = []

    @property
    def scope(self):
        return (self.tenant_id, self.user.id)

    def run(self, broker: FakeBroker, **kw) -> dh.PassReport:
        def factory(record):
            self.used.append(record.id)
            return broker
        kw.setdefault("wrap", lambda inner, record: inner)
        return _run(dh.run_pass(_session_factory, now=NOW, adapter_factory=factory, clock=lambda: NOW, pause=0, **kw))


def _broker(symbol: str, base: float, *, auction: bool = False, fail=None) -> FakeBroker:
    daily = daily_bars(date(2022, 1, 3), FRI, base)
    minutes = minute_bars(THU, base) + minute_bars(FRI, base, after_close=10 if auction else 0)
    if auction:                                            # Friday's Daily extreme and close come from the closing auction
        cont = [b for b in minutes if b.timestamp.astimezone(IST).date() == FRI and b.timestamp.astimezone(IST).time() < time(15, 30)]
        hi, lo, last = max(b.high for b in cont), min(b.low for b in cont), cont[-1].close
        daily[-1] = OHLCVBar(timestamp=datetime.combine(FRI, time(0, 0), tzinfo=IST), open=cont[0].open, high=hi + 40, low=lo,
                             close=last + 12)
    return FakeBroker({symbol: daily}, {symbol: minutes}, fail=fail)


# --- DH1: where the rows go, idempotent, resumable -------------------------------------------------------------------------
def test_backfill_writes_only_the_owners_private_store_through_his_own_session_and_reruns_idempotently(monkeypatch):
    owner = Owner("dh-own@example.com", monkeypatch, indices=("NIFTY",))
    key = dh.target("NIFTY").key
    assert key == "NSE_INDEX:NIFTY50" == copilot_tools.lake_key("NIFTY")       # the key the chart reader asks for
    broker = _broker("NIFTY 50", 17000)
    report = owner.run(broker)
    assert report.status == "ok" and report.failed == 0 and report.complete, report.lines()
    assert owner.used == [owner.credential_id] and report.tenant_id == owner.tenant_id and report.holder == owner.user.id
    source = f"broker:upstox:u{owner.user.id}"
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.tenant_id == owner.tenant_id, MdTenantCandleRecord.instrument_key == key,
                  MdTenantCandleRecord.timeframe == "day", MdTenantCandleRecord.source == source) > 200
    assert _count(MdTenantCandleRecord, MdTenantCandleRecord.tenant_id == owner.tenant_id, MdTenantCandleRecord.timeframe == "1m",
                  MdTenantCandleRecord.instrument_key == key) == 750                       # Thursday + Friday (today's endpoint)
    # the shared lake is never written (scoped to this owner's source: other tests share this DB and write their own rows)
    assert _count(MdCandleRecord, MdCandleRecord.source == source) == 0
    assert any(c[0] == "intraday" for c in broker.calls)                                    # a session that closed today
    first_calls = len(broker.calls)
    again = owner.run(broker)                                                                # the re-run: only the growing windows
    assert again.inserted == 0 and again.corrected == 0 and again.skipped > 0 and again.failed == 0
    assert len(broker.calls) - first_calls < first_calls / 2
    done = _count(W, W.tenant_id == owner.tenant_id, W.status == "done")
    assert done >= 1 and _count(W, W.tenant_id == owner.tenant_id, W.status == "no_data") >= 1
    assert _run(_if_missing(owner)).status == "nothing_missing"                             # the deploy step exits at once


async def _if_missing(owner):
    return await dh.run_pass(_session_factory, now=NOW, if_missing=True, adapter_factory=lambda r: None, wrap=lambda i, r: i, pause=0)


def test_resume_after_a_stop_asks_only_what_is_left(monkeypatch):
    owner = Owner("dh-resume@example.com", monkeypatch, indices=("BANKNIFTY",))
    broker = _broker("NIFTY BANK", 40000)
    first = owner.run(broker, max_requests=3)
    assert first.status == "time_budget" and first.remaining > 0 and first.requests == 3
    asked_first = list(broker.calls)

    async def finished():
        async with _session_factory() as session:
            return {("day" if r.timeframe == "day" else "1min", r.window_start) for r in await session.scalars(select(W).where(
                W.tenant_id == owner.tenant_id, W.status.in_(("done", "no_data"))))}
    done_first = _run(finished())
    assert done_first
    second = owner.run(broker)
    assert second.status == "ok" and second.complete and second.remaining == 0
    later = [(c[3], c[4]) for c in broker.calls[len(asked_first):] if c[0] == "historical"]
    # a window finished in the first pass is never asked again; every window is asked at least once over the two passes
    assert not [w for w in later if (w[0], date(w[1].year, 1, 1) if w[0] == "day" else w[1]) in done_first]
    assert second.requests + first.requests >= len(dh.windows("day", date(2022, 3, 10), FRI)) + len(dh.windows("1m", FRI - timedelta(days=45), FRI))


def test_a_refused_rate_token_ends_the_pass_and_the_next_one_resumes(monkeypatch):
    owner = Owner("dh-rate@example.com", monkeypatch, indices=("FINNIFTY",))
    broker = _broker("NIFTY FIN SERVICE", 19000, fail={2: BudgetUsed("broker rate budget used")})
    first = owner.run(broker)
    assert first.status == "rate_limited" and first.remaining > 0 and "resumes" in " ".join(first.notes)
    broker.fail = {}
    assert owner.run(broker).complete


def test_default_requests_take_the_shared_api_budget():
    from app.market_data.api_budget import BudgetedAdapter

    class Rec:
        id, broker_name = 1, "upstox"
    assert isinstance(dh._budgeted(object(), Rec()), BudgetedAdapter)


def test_no_session_of_the_owner_means_nothing_is_read_never_a_teammates(monkeypatch):
    owner = Owner("dh-nosess@example.com", monkeypatch, credential=False)
    mate = _teammate(owner.user, "dh-nosess-mate@example.com")
    _credential(mate)                                                    # a teammate's own session is never used
    broker = _broker("NIFTY 50", 17000)
    report = owner.run(broker)
    assert report.status == "no_session" and broker.calls == [] and owner.used == []
    monkeypatch.setattr(config, "HISTORY_OWNER_TENANT_ID", None)
    assert owner.run(broker).status == "not_configured" and broker.calls == []


def test_broker_messages_are_scrubbed_of_anything_token_like():
    assert "[...]" in dh.scrub("401 for token eyJhbGciOiJIUzI1NiJ9abcdefghijklmnop") and "eyJ" not in dh.scrub("eyJhbGciOiJIUzI1NiJ9abcdefghijklmnop")


def test_windows_are_fixed_and_newest_first():
    days = dh.windows("1m", date(2023, 1, 24), FRI)
    assert days[0] == (date(2023, 3, 6), date(2023, 3, 12)) and all(a.weekday() == 0 for a, _ in days)
    assert days[-1][0] <= date(2023, 1, 24) <= days[-1][1]
    assert dh.windows("day", date(2022, 3, 10), FRI) == [(date(2023, 1, 1), date(2023, 12, 31)), (date(2022, 1, 1), date(2022, 12, 31))]


# --- DH3: the closing auction --------------------------------------------------------------------------------------------
def test_auction_flags_rule_is_pure_and_generic():
    from app.market_lake.ingest import LakeBar
    key = "NSE_INDEX:X"
    close = dh.session_close(FRI)
    mins = [LakeBar(key, "1m", close - timedelta(minutes=2), 100, 105, 99, 104), LakeBar(key, "1m", close, 104, 106, 103, 105),
            LakeBar(key, "1m", close + timedelta(minutes=1), 105, 105, 105, 105)]
    daily = {FRI: LakeBar(key, "day", close, 100, 110, 95, 107)}
    kinds = {(f.kind, f.timeframe): f for f in dh.auction_flags(key, daily, mins)}
    assert kinds[("post_close", "1m")].detail.startswith("flat") and kinds[("auction_high", "day")].value == 106
    assert kinds[("auction_low", "day")].value == 99 and kinds[("official_close", "day")].value == 107
    assert dh.auction_flags(key, {FRI: LakeBar(key, "day", close, 100, 106, 99, 105)}, mins[:2]) == []   # nothing to flag


def test_auction_flags_keep_the_bars_out_of_structure_and_zones(monkeypatch):
    owner = Owner("dh-auction@example.com", monkeypatch, indices=("BANKEX",))
    t = dh.target("BANKEX")
    assert t.key == "BSE_INDEX:BANKEX" and t.exchange == "BSE"
    broker = _broker("BANKEX", 50000, auction=True)
    report = owner.run(broker)
    assert report.flags >= 12, report.lines()                         # 10 post-close bars, the auction high, the official close
    fri_end = dh.session_close(FRI)
    cont = [b for b in broker.minutes["BANKEX"] if b.timestamp.astimezone(IST).date() == FRI and b.timestamp.astimezone(IST).time() < time(15, 30)]
    cont_high = max(b.high for b in cont)
    official = broker.daily["BANKEX"][-1].close
    assert _count(MdBarFlagRecord, MdBarFlagRecord.tenant_id == owner.tenant_id, MdBarFlagRecord.kind == "post_close") == 10

    async def reads():
        from app.charts import price_action_layer as pal
        from app.price_action.zones import job
        from app.price_action.zones.store import platform_settings
        async with _session_factory() as session:
            lo, hi = datetime(2023, 1, 1, tzinfo=timezone.utc), NOW.astimezone(timezone.utc)
            day_rows = await own_bars.fetch_rows(session, owner.scope, t.key, "day", lo, hi)
            m1 = await own_bars.fetch_rows(session, owner.scope, t.key, "1m", lo, hi)
            daily, dsrc = await pal.load_frame(session, t.key, "1d", lo, hi, private=owner.scope)
            q15, qsrc = await pal.load_frame(session, t.key, "15m", datetime(2023, 3, 1, tzinfo=timezone.utc), hi, private=owner.scope)
            frames = await job.load_frames(session, t.key, platform_settings(), hi, private=owner.scope)
            close = await own_bars.official_close(session, t.key, owner.scope)
            return day_rows, m1, daily, dsrc, q15, qsrc, frames, close
    day_rows, m1, daily, dsrc, q15, qsrc, frames, close = _run(reads())
    fri = [r for r in day_rows if r[0].replace(tzinfo=timezone.utc) == fri_end][0]
    assert fri[3] == cont_high and fri[5] <= cont_high                  # the auction high is not read; the close held inside
    assert len(m1) == 750                                               # the post-close flat bars are dropped
    assert dsrc == "own:day" and float(daily["high"].iloc[-1]) == cont_high
    assert qsrc == "own:1m" and len(q15) == 50                          # 25 bars a session, none after continuous trading
    assert all(pd.Timestamp(e).tz_convert(IST).time() <= time(15, 30) for e in q15["bar_end"])
    zone_daily = frames.frames.get("1d")
    assert zone_daily is not None and float(zone_daily["high"].iloc[-1]) == cont_high      # the zones job reads the same capped bar
    assert close == {"close": round(official, 2), "date": FRI.isoformat(), "source": "your broker's Daily history"}   # DH6: the official close


# --- DH4: health ----------------------------------------------------------------------------------------------------------
def test_health_lines_flag_missing_and_short(monkeypatch):
    owner = Owner("dh-health@example.com", monkeypatch, indices=("MIDCPNIFTY", "NIFTYNXT50"))
    owner.run(FakeBroker({"NIFTY MID SELECT": daily_bars(date(2022, 1, 3), FRI, 10000)},
                         {"NIFTY MID SELECT": minute_bars(THU, 10000) + minute_bars(FRI, 10000)}))

    async def cover():
        async with _session_factory() as session:
            return await dh.coverage(session, owner.scope, FRI)
    rows = {(c.name, c.tf): c for c in _run(cover())}
    assert rows[("MIDCPNIFTY", "D")].flag == "ok" and rows[("MIDCPNIFTY", "D")].source == "own" and rows[("MIDCPNIFTY", "D")].last == FRI
    assert rows[("MIDCPNIFTY", "W")].bars >= 52 and rows[("MIDCPNIFTY", "1H")].bars == 14 and rows[("MIDCPNIFTY", "15M")].bars == 50
    assert rows[("MIDCPNIFTY", "1H")].flag == "SHORT" and "starts 2023-03-09" in rows[("MIDCPNIFTY", "1H")].why
    assert all(rows[("NIFTYNXT50", tf)].flag == "MISSING" for tf in dh.HEALTH_TFS)
    line = rows[("MIDCPNIFTY", "D")].line()
    assert "MIDCPNIFTY" in line and " D " in line and "bars" in line and f"{FRI}" in line and len(line) < 100
    lines = _run(dh._health_lines(_session_factory, NOW))
    assert "MISSING / SHORT" in lines[0] and len(lines) == 1 + 2 * len(dh.HEALTH_TFS)


def test_atp_sh_has_the_owner_commands_and_the_health_step():
    from pathlib import Path
    sh = (Path(__file__).resolve().parents[2] / "deploy" / "atp.sh").read_text()
    assert "lake-backfill)  cmd_lake_backfill" in sh and "dc exec -T backend python -m app.market_lake.index_history check" in sh
    assert "lake_report" in sh and "run --if-missing --budget-seconds 120" in sh and "#   /opt/atp/deploy/atp.sh lake-backfill [--check]" in sh


# --- DH5: honest wording --------------------------------------------------------------------------------------------------
def _structure(rows, *, official=None, last_close=24850.5):
    layer = {"instrument": "NSE_INDEX:NIFTY50", "tf": "1d", "as_of": "2026-10-23T10:00:00+00:00", "bars": rows[1][1], "last_close": last_close,
             "mtf": [{"tf": tf, "direction": "none" if n < 30 else "up", "bars": n, "protected": None, "last_event": None} for tf, n in rows],
             "structure_state": {"direction": "none"}}
    data = copilot_tools.summarise_structure(layer)
    data["official_close"] = official
    return ToolResult(True, {"symbol": "NIFTY", **data}, data["as_of"], "price_action_layer:lake", {})


def _runner(results):
    async def run(name, args):
        return results[name]
    return run


def test_the_copilot_says_the_history_is_short_without_internal_counts(monkeypatch):
    """DH5 + M59 CR-1: a short history is said as such - "Daily: data not ready (history is loading)" - never as
    "no structure", and never with the bar counts ("only 7 of the 30 ...")."""
    monkeypatch.setattr(config, "LAKE_BACKFILL_INDICES", ["NIFTY", "BANKNIFTY"])
    res = _structure([("1w", 2), ("1d", 7), ("1h", 0), ("15m", 0)])
    zones = ToolResult(False, None, None, "market_zones", error="no zones for this instrument")
    out = _run(chat.answer("Read the NIFTY chart", _runner({"get_chart_structure": res, "get_market_zones": zones})))
    text = " ".join(out.summary + [x for s in out.sections for x in s["lines"]] + out.unknowns)
    assert "Daily: data not ready (history is loading)" in out.summary[0]
    assert "Daily: data not ready (history is loading)" in out.sections[0]["lines"]
    assert "15-minute: data not ready (history is loading)" in text
    assert "they are built from the daily history. Daily: data not ready (history is loading)" in text
    assert not re.search(r"of the \d+|bars needed|\d+ of \d+ bars|only \d+", json.dumps(out.as_dict()))
    assert "structure reads none" not in text and "no clear trend" not in text and "no zones" not in text.lower().replace("no zones for", "")
    table = next(b for b in out.blocks if b["type"] == "table")["table"]
    assert table["rows"][1]["direction"] == "data not ready"


def test_an_empty_chart_says_the_history_is_loading_not_that_it_cannot_read(monkeypatch):
    monkeypatch.setattr(config, "LAKE_BACKFILL_INDICES", ["NIFTY"])
    from app.context_panel.service import short_history
    err = short_history(0, 30, "daily bars", "NSE_INDEX:NIFTY50")
    assert err == "Daily: data not ready (history is loading)"
    assert short_history(3, 30, "daily bars", "NSE:SOMESHARE") == "Daily: data not ready (more history is needed)"
    assert short_history(0, 30, "1-hour bars", "NSE_INDEX:NIFTY50") == "1-hour: data not ready (history is loading)"
    res = ToolResult(False, None, None, "price_action_layer", error=err)
    zones = ToolResult(False, None, None, "market_zones", error="no zones for this instrument")
    out = _run(chat.answer("Read the NIFTY chart", _runner({"get_chart_structure": res, "get_market_zones": zones})))
    assert out.summary == ["I can't read the NIFTY chart yet. Daily: data not ready (history is loading)."]
    assert "I don't know the NIFTY structure yet. Daily: data not ready (history is loading)." in out.unknowns
    assert any("key areas yet: they are built from the daily history" in u for u in out.unknowns)


def test_the_market_line_and_the_regime_tile_say_the_history_is_short(monkeypatch):
    from app.context_panel import service
    monkeypatch.setattr(config, "LAKE_BACKFILL_INDICES", ["NIFTY"])
    seven = pd.DataFrame({"high": [1.0] * 7, "low": [0.5] * 7, "close": [0.8] * 7}, index=pd.date_range("2023-03-01", periods=7))

    async def bars(*a, **k):
        return seven

    async def none(*a, **k):
        return None
    monkeypatch.setattr(service, "_bars", bars)
    monkeypatch.setattr(service, "_own_daily", none)
    tile = _run(service.regime_tile(None, service.Viewer(1, 1), FRI, []))
    assert tile["status"] == "na" and tile["reason"] == "Daily: data not ready (history is loading)"
    ctx = ToolResult(True, copilot_tools.summarise_context({"as_of": "x", "tiles": {"regime": tile}}), "x", "context_panel", {})
    line = _run(chat.market_line(_runner({"get_market_context": ctx})))
    assert line["line"] is None and line["reason"] == "Today's market line is not ready: Daily: data not ready (history is loading)."


# --- DH6: the last close ----------------------------------------------------------------------------------------------------
def test_the_copilot_states_the_official_close_with_its_date_and_source(monkeypatch):
    monkeypatch.setattr(config, "LAKE_BACKFILL_INDICES", ["NIFTY"])
    res = _structure([("1w", 120), ("1d", 700), ("1h", 900), ("15m", 900)],
                     official={"close": 25123.45, "date": "2026-10-23", "source": "NSE index close file"})
    zones = ToolResult(True, {"bands": []}, "x", "market_zones", {})
    out = _run(chat.answer("Read the NIFTY chart", _runner({"get_chart_structure": res, "get_market_zones": zones})))
    assert out.summary[0].startswith("NIFTY last closed at 25,123.45 on 23 Oct 2026 (official close, NSE index close file).")
    assert {"label": "close", "value": 25123.45} in out.worked[0].numbers


def test_official_close_reads_the_stored_daily_bar_and_never_another_members(monkeypatch):
    owner = Owner("dh-close@example.com", monkeypatch)
    mate = _teammate(owner.user, "dh-close-mate@example.com")
    other = _user("dh-close-other@example.com")
    key = "NSE_INDEX:DHSIXTEST"

    async def seed():
        async with _session_factory() as session:
            session.add(MdCandleRecord(instrument_key=key, timeframe="day", ts=dh.session_close(THU), source="nse_bhav:index", version=1,
                                       open=1, high=2, low=0.5, close=22612.35, volume=0))
            session.add(MdTenantCandleRecord(tenant_id=owner.tenant_id, instrument_key=key, timeframe="day", ts=dh.session_close(FRI),
                                             source=f"broker:upstox:u{owner.user.id}", version=1, open=1, high=2, low=0.5, close=22650.1,
                                             volume=0))
            await session.commit()
            return (await own_bars.official_close(session, key, owner.scope),
                    await own_bars.official_close(session, key, await own_bars.scope_for(session, mate, key)),
                    await own_bars.official_close(session, key, await own_bars.scope_for(session, other, key)),
                    await own_bars.scope_for(session, mate, key), await own_bars.scope_for(session, other, key))
    mine, mates, others, mate_scope, other_scope = _run(seed())
    assert mine == {"close": 22650.1, "date": FRI.isoformat(), "source": "your broker's Daily history"}
    assert mates == others == {"close": 22612.35, "date": THU.isoformat(), "source": "NSE index close file"}
    assert mate_scope is None and other_scope is None                   # the holder's bars are his alone
    assert dh.round_to(22600.0, 50) and not dh.round_to(22612.35, 50)


# --- tenancy of the zones and the worker ---------------------------------------------------------------------------------------
def test_holder_zone_scope_is_read_only_by_the_holder(monkeypatch):
    from app.price_action.zones import job, read
    assert job.holder_scope(3, 7) == "own:3:u7" and job.holder_of("own:3:u7") == (3, 7)
    assert job.holder_of("platform") is None and job.holder_of("tenant:3") is None
    assert len(job.holder_scope(99999, 9999999)) <= 24                  # fits market_zone_runs.scope

    async def other():
        async with _session_factory() as session:
            return await read.read_holder(session, "NSE_INDEX:NIFTY50", NOW, 1, 999999)
    assert _run(other()) is None


def test_worker_pass_is_due_once_a_day_after_the_hour(monkeypatch):
    monkeypatch.setattr(config, "LAKE_BACKFILL_HOUR_IST", 16)
    assert dh.nightly_due(datetime(2023, 3, 10, 15, 0, tzinfo=IST), None) is None
    assert dh.nightly_due(datetime(2023, 3, 10, 16, 5, tzinfo=IST), None) == FRI
    assert dh.nightly_due(datetime(2023, 3, 10, 20, 0, tzinfo=IST), FRI) is None


def test_migration_is_additive_rollback_safe_and_the_only_head():
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    backend = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(backend / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1 and "d4a6c8e0b2f1" in {r.revision for r in script.walk_revisions("base", heads[0])}
    text_ = (backend / "alembic" / "versions" / "d4a6c8e0b2f1_data_h_lake_backfill.py").read_text()
    assert "ROLLBACK_SAFE = True" in text_ and "op.alter_column" not in text_ and "op.drop_column" not in text_

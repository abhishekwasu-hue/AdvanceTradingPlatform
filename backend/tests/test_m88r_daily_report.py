"""M88-R: the shared daily AI market report (app/daily_report).

* The universe: "<index> constituents" is read from NSE's official list (a mocked source; never typed into code), the
  last good list cached and used when NSE cannot be reached; an empty setting makes no report.
* One AI request per run (a scripted reply - no paid call); the output check refuses buy / sell / target / stop / entry /
  should and asks once more; a second refusal stores nothing and the admin sees why.
* Users read the stored report without any AI call; the flag OFF answers 404.
* The schedule: due once the day's bhavcopy is stored, not while HB1 is still loading it, not twice; a failed run is
  retried after its retry time; the admin's Run now.
Fake lake data only.
"""
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.daily_report import build, check, info_line, job
from app.daily_report import settings as S
from app.daily_report.universe import expand
from app.db.models import HistoryBackfillFileRecord, MdCandleRecord, PlatformControlRecord, SharedDailyReportRecord, User
from app.market_lake import ingest
from app.market_lake.bhavcopy_backfill import bar_end
from app.platform import controls, flag_catalog, flag_state
from app.universe.sources import StaticSource
from tests.test_admin_api import _admin
from tests.test_auth_api import _session_factory, client
from tests.test_deployments_api import _auth
from tests.test_settings_a_connect_ai import _flags

STOCKS = ("M88RAAA", "M88RBBB", "M88RCCC")
NSE_LIST = (b"Company Name,Industry,Symbol,Series,ISIN Code\n"
            b"Alpha Ltd.,Financial Services,M88RAAA,EQ,INE000A01011\n"
            b"Beta Ltd.,Information Technology,M88RBBB,EQ,INE000B01011\n"
            b"Gamma Ltd.,Financial Services,M88RCCC,EQ,INE000C01011\n")
DAY = date(2026, 11, 6)
CLEAN = {"index_read": "The index closed higher; the Daily structure is up.", "breadth_read": "Two stocks rose and one fell.",
         "sectors_read": "Financial services led.", "leaders_laggards": "M88RAAA led; M88RBBB lagged.",
         "notable_structures": "M88RCCC is near a demand area.", "conclusion": "A firm close with broad participation.",
         "stocks": {s: f"{s} closed with its structure unchanged." for s in STOCKS}}


def _run(coro):
    return asyncio.run(coro)


def _weekdays(n: int, last: date) -> list[date]:
    out, d = [], last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


DAYS = _weekdays(120, DAY)


def _seed() -> None:
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.in_(
                ["NSE_INDEX:NIFTY50", *[f"NSE:{s}" for s in STOCKS]])))
            await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key.startswith("NFO:M88RAAA:")))
            await session.commit()
            import math
            for key, base, source in (("NSE_INDEX:NIFTY50", 24000.0, "nse_bhav:index"), ("NSE:M88RAAA", 1000.0, "nse_bhav:cm"),
                                      ("NSE:M88RBBB", 500.0, "nse_bhav:cm"), ("NSE:M88RCCC", 200.0, "nse_bhav:cm")):
                bars = []
                for i, d in enumerate(DAYS):
                    c = base * (1 + 0.04 * math.sin(i / 6.0) + 0.0005 * i)
                    bars.append(ingest.LakeBar(key, "day", bar_end(d), c * 0.998, c * 1.01, c * 0.99, c, 100000 + 500 * i))
                await ingest.write_candles_bulk(session, bars, source)
            fut = [ingest.LakeBar("NFO:M88RAAA:FUT:2026-11-26", "day", bar_end(d), 1, 1, 1, 1, 0, oi) for d, oi in ((DAYS[-2], 1000), (DAYS[-1], 1200))]
            await ingest.write_candles_bulk(session, fut, "nse_bhav:fo")
            await session.commit()
    _run(go())


@dataclass
class Reply:
    text: str
    input_tokens: int = 3000
    output_tokens: int = 900
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model: str = ""
    stop_reason: str = "end_turn"


class Scripted:
    """A recorded reply per request - no provider, no paid call. Counts the requests like UserKeyProvider.spent."""

    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.prompts: list[str] = []
        self.spent = {"calls": 0, "tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0}

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 2000):
        self.prompts.append(user)
        reply = Reply(self.texts[min(len(self.prompts), len(self.texts)) - 1])
        self.spent["calls"] += 1
        self.spent["tokens_input"] += reply.input_tokens
        self.spent["tokens_output"] += reply.output_tokens
        self.spent["cost_usd"] += 0.01
        return reply


def _setting(symbols, user_id=None) -> None:
    async def go():
        async with _session_factory() as session:
            await controls._set(session, S.KEY, {"symbols": symbols, "recent_bars": 20, "key_user_id": user_id}, None)
            await controls._set(session, S.LIST_KEY, {}, None)
            await session.execute(delete(SharedDailyReportRecord))
            await session.commit()
    _run(go())


def _report(provider, *, day=DAY, source=None, trigger="schedule"):
    async def go():
        async with _session_factory() as session:
            user = await session.scalar(select(User).where(User.email == RUNNER))
            row = await build.run(session, day, user=user, trigger=trigger, provider=provider,
                                  source=source or StaticSource({"constituents:NIFTY50": NSE_LIST}))
            return row.id, row.status, row.error, row.content_json, row.ai_requests, row.input_tokens + row.output_tokens
    return _run(go())


RUNNER = "m88r-runner@example.com"


@pytest.fixture(scope="module", autouse=True)
def runner():
    _auth(RUNNER)


@pytest.fixture
def seeded():
    _seed()
    _setting(["NIFTY 50", "NIFTY 50 CONSTITUENTS"])
    yield
    _setting([])
    _flags(ui_v2=False, shared_daily_report=False)


def test_flag_registered_off_by_default_with_catalog_and_state():
    assert "shared_daily_report" in controls.FEATURE_FLAGS and "shared_daily_report" in controls.DEFAULT_OFF_FLAGS
    assert flag_catalog.FLAG_INFO["shared_daily_report"].depends_on == ("ui_v2",)
    assert flag_state.status_for("shared_daily_report").state == "needs_setup"


def test_universe_is_read_from_the_nse_list_and_the_last_good_list_is_kept(seeded):
    async def go():
        async with _session_factory() as session:
            first = await expand(session, ["NIFTY 50", "NIFTY 50 CONSTITUENTS"], StaticSource({"constituents:NIFTY50": NSE_LIST}))
            second = await expand(session, ["NIFTY 50", "NIFTY 50 CONSTITUENTS"], StaticSource({}))   # NSE not reached
            empty = await expand(session, ["NIFTY BANK CONSTITUENTS"], StaticSource({}))
            return first, second, empty
    first, second, empty = _run(go())
    assert [i.symbol for i in first.items] == ["NIFTY 50", *STOCKS] and first.items[0].kind == "INDEX"
    assert first.items[1].sector == "Financial Services" and first.list_source == {"NIFTY50": "nse"}
    assert [i.symbol for i in second.items] == ["NIFTY 50", *STOCKS] and second.list_source == {"NIFTY50": "cached"}
    assert empty.items == [] and "could not be read from NSE" in empty.problems[0]


def test_an_empty_setting_makes_no_report(seeded):
    _setting([])
    with pytest.raises(build.NothingToRun):
        _report(Scripted(json.dumps(CLEAN)))


def test_one_ai_request_builds_and_stores_the_report(seeded):
    ai = Scripted(json.dumps(CLEAN))
    _id, status, error, content, requests, tokens = _report(ai)
    assert status == "ok" and error is None and requests == 1 and len(ai.prompts) == 1 and tokens == 3900
    report = json.loads(content)
    assert report["info_line"] == info_line("2026-11-06") == "Last close 6 Nov 2026 · AI-generated · information only, not advice"
    assert [s["symbol"] for s in report["stocks"]] == list(STOCKS) and report["index"][0]["symbol"] == "NIFTY 50"
    assert report["stocks"][0]["read"] == "M88RAAA closed with its structure unchanged."
    assert report["stocks"][0]["fut_oi_change_pct"] == 20.0 and report["stocks"][0]["delivery_pct"] is None
    assert report["breadth"]["stocks"] == 3 and {s["sector"] for s in report["sectors"]} == {"Financial Services", "Information Technology"}
    # the ONE table the AI read: EOD figures only, every symbol once
    prompt = ai.prompts[0]
    assert prompt.startswith("Last close 2026-11-06.") and all(prompt.count(f"\n{s}|") == 1 for s in STOCKS)


def test_recommendation_words_are_refused_and_asked_once_more(seeded):
    bad = dict(CLEAN, conclusion="Traders should buy above the high with a stop below.")
    ai = Scripted(json.dumps(bad), json.dumps(CLEAN))
    _id, status, _error, content, requests, _ = _report(ai)
    assert status == "ok" and requests == 2 and "refused" in ai.prompts[1] and "should" in ai.prompts[1]
    assert check.banned_words(json.loads(content)["sections"]) == []


def test_two_refusals_store_nothing_and_say_why(seeded):
    bad = dict(CLEAN, stocks={"M88RAAA": "A clean entry with a target near the high."})
    _id, status, error, content, requests, _ = _report(Scripted(json.dumps(bad), "not json at all"))
    assert status == "failed" and content is None and requests == 2 and "refused twice" in error and "Nothing was stored" in error


def test_the_check_reads_every_word_form():
    assert check.banned_words({"a": "Sellers stepped in", "b": ["stop-loss", "Targets"], "c": "SHOULD hold"}) == [
        "sellers", "stop", "targets", "should"]
    assert check.banned_words({"a": "The demand area held; structure is up."}) == []


def test_users_read_the_stored_report_without_an_ai_call(seeded, monkeypatch):
    report_id, *_ = _report(Scripted(json.dumps(CLEAN)))
    headers = _auth("m88r-reader@example.com")
    assert client.get("/api/daily-report/latest", headers=headers).status_code == 404          # flag off
    _flags(ui_v2=True, shared_daily_report=True)

    async def no_ai(*a, **k):
        raise AssertionError("a user's read never calls the AI")
    from app.ai import resolve
    monkeypatch.setattr(resolve, "user_key_provider", no_ai)
    body = client.get("/api/daily-report/latest", headers=headers).json()["report"]
    assert body["id"] == report_id and body["trade_date"] == "2026-11-06" and len(body["stocks"]) == 3
    pdf = client.get(f"/api/daily-report/{report_id}/pdf", headers=headers)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and b"Last close 6 Nov 2026" in pdf.content


def _row(**values) -> None:
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(SharedDailyReportRecord))
            if values:
                session.add(SharedDailyReportRecord(trade_date=DAY, **values))
            await session.commit()
    _run(go())


def _due(now):
    async def go():
        async with _session_factory() as session:
            return await job.due_day(session, now)
    return _run(go())


def test_runs_once_after_the_bhavcopy_with_retry(seeded):
    now = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)
    _setting(["NIFTY 50", "NIFTY 50 CONSTITUENTS"], user_id=1)
    assert _due(now) is None                                             # flag off: nothing
    _flags(shared_daily_report=True)
    assert _due(now) == DAY                                              # the day's close is stored, no report yet

    async def loading(status):
        async with _session_factory() as session:
            await session.execute(delete(HistoryBackfillFileRecord).where(HistoryBackfillFileRecord.trade_date == DAY))
            if status:
                session.add(HistoryBackfillFileRecord(segment="fo", trade_date=DAY, status=status))
            await session.commit()
    _run(loading("running"))
    assert _due(now) is None                                             # HB1 still loading that day's F&O file
    _run(loading(None))
    _row(status="ok", attempts=1, content_json="{}")
    assert _due(now) is None                                             # stored once: never twice
    _row(status="failed", attempts=1, next_try_at=now + timedelta(minutes=10), updated_at=now)
    assert _due(now) is None and _due(now + timedelta(minutes=11)) == DAY  # retried after its retry time
    _row(status="failed", attempts=99, next_try_at=None, updated_at=now)
    assert _due(now + timedelta(hours=2)) is None                        # at most DAILY_REPORT_MAX_ATTEMPTS a day
    _row()


def test_run_due_uses_the_admins_key_and_stores_once(seeded):
    headers, me = _admin("m88r-admin-due@example.com")
    _setting(["NIFTY 50", "NIFTY 50 CONSTITUENTS"], user_id=me["id"])
    _flags(shared_daily_report=True)
    ai = Scripted(json.dumps(CLEAN))
    src = StaticSource({"constituents:NIFTY50": NSE_LIST})
    first = _run(job.run_due(_session_factory, provider=ai, source=src))
    again = _run(job.run_due(_session_factory, provider=ai, source=src))
    assert first == {"ran": True, "day": "2026-11-06", "status": "ok"} and again == {"ran": False, "telegram": None} and len(ai.prompts) == 1


def test_admin_card_settings_and_run_now(seeded, monkeypatch):
    headers, me = _admin("m88r-admin@example.com")
    member = _auth("m88r-member@example.com")
    assert client.get("/api/admin/daily-report", headers=member).status_code == 403
    saved = client.put("/api/admin/daily-report/settings", headers=headers,
                       json={"symbols": ["nifty 50", "NIFTY 50 constituents"], "recent_bars": 20}).json()
    assert saved["settings"]["symbols"] == ["NIFTY 50", "NIFTY 50 CONSTITUENTS"] and saved["key"]["connected"] is False
    assert client.post("/api/admin/daily-report/run", headers=headers).status_code == 409           # no own AI key yet

    async def key_ok(session, user):
        return {"connected": True, "message": None}
    from app.daily_report import routes, universe
    monkeypatch.setattr(routes, "_key_state", key_ok)
    ai = Scripted(json.dumps(CLEAN))

    async def provider(session, user, client=None):
        return ai
    monkeypatch.setattr(build, "_provider", provider)
    monkeypatch.setattr(universe, "default_source", lambda: StaticSource({"constituents:NIFTY50": NSE_LIST}))
    started = client.post("/api/admin/daily-report/run", headers=headers).json()
    assert started == {"started": True, "trade_date": "2026-11-06"}
    runs = client.get("/api/admin/daily-report", headers=headers).json()["runs"]
    assert runs[0]["status"] == "ok" and runs[0]["trigger"] == "run_now" and runs[0]["ai_requests"] == 1 and runs[0]["tokens"] == 3900
    assert runs[0]["cost_inr"] > 0 and len(ai.prompts) == 1


def test_settings_rows_live_in_platform_controls_only(seeded):
    async def go():
        async with _session_factory() as session:
            return await session.get(PlatformControlRecord, S.KEY)
    assert _run(go()) is not None

"""M86 W1 / W2 / W4 (docs/design/M86_PLAN.md, "Public side"): the chart reader's public boundary on the test app.

The private reader is NOT imported here: a synthetic fake reader (`loader.set_for_tests`) and a fake API client stand in
for it. Made-up data only (the M59 hybrid test's fixture candles); no network, no API key, no method text.
"""
import hashlib
import json
import logging
import sys
import types
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.cache import client as cache_client
from app.chart_reader import FLAG, loader, service
from app.chart_reader import settings as settings_mod
from app.db.models import LlmCallRecord, MdCandleRecord
from app.platform import controls, flag_catalog, flag_state
from tests.test_auth_api import _session_factory, client
from tests.test_chart_analyze_api import _owner, _run
from tests.test_chart_analyze_api import _upload as _ca_upload
from tests.test_chart_analyze_hybrid import SYMBOL, F, _allow, _isolated, _lake, _zones  # noqa: F401 - fixtures

BASE = "/api/chart-reader"
PAIR_SYMBOL = f"HYP{uuid.uuid4().hex[:6].upper()}"
PAIR_KEY = f"NSE:{PAIR_SYMBOL}"
FAKE_PROMPT = "FAKE-PRIVATE-PROMPT-m86-zq91"         # what the fake reader "sends"; must never reach ATP's DB or logs
FAKE_ANSWER = "FAKE-PRIVATE-ANSWER-m86-zq92"


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return FAKE_ANSWER


class FakeReader:
    name = "chart_reader"
    version = "fake_read_v1+fake_book_v1"
    ABHI = "ABHI"

    def __init__(self, *, status: str = "ok", raises: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.status = status
        self.raises = raises

    def strategies(self):
        return [{"id": "ABHI", "name": "Abhi's method"},
                {"id": "BOOK_ONE", "name": "Book One strategy", "book": "Book One", "author": "A. Author", "edition": "1900",
                 "two_chart": False, "sides": ["buyer"]},
                {"id": "BOOK_TWO", "name": "Book Two strategy", "book": "Book Two", "author": "B. Author", "edition": "1910",
                 "two_chart": True, "sides": ["buyer", "seller"]}]

    def two_chart_conditions(self, strategy):
        return ["the paired average confirms the move"] if strategy == "BOOK_TWO" else []

    def read(self, *, instrument, charts, facts, strategy, model, effort, client):
        self.calls.append({"instrument": instrument, "charts": charts, "facts": facts, "strategy": strategy, "model": model,
                           "effort": effort})
        logging.getLogger("fake.private.reader").debug("built the private request")
        client.create(system=FAKE_PROMPT, content=[c[0] for c in charts])
        if self.raises is not None:
            raise self.raises
        two = self.two_chart_conditions(strategy)
        return {"status": self.status, "strategy": {"id": strategy, "name": "Fake", "independent_book_strategy": strategy != "ABHI"},
                "prompt_version": "fake_read_v1", "read": {"trend": "up", "statement": "area of interest below"}, "removed": [],
                "time_flags": [], "two_chart_conditions": two, "second_chart_missing": bool(two) and len(charts) < 2,
                "usage": {"input_tokens": 5000, "output_tokens": 1500, "cache_read_input_tokens": 2000, "model": model}}


async def _drop_pair() -> None:
    async with _session_factory() as session:
        await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == PAIR_KEY))
        await session.execute(delete(MdCandleRecord).where(MdCandleRecord.instrument_key == f"NSE:{SYMBOL}", MdCandleRecord.timeframe == "1h"))
        await session.commit()


def _hourly():
    """The fixture's 15-minute candles folded into 1-hour candles (four at a time), so the 1-hour chart has stored bars."""
    rows = []
    for i in range(0, len(F) - len(F) % 4, 4):
        part = F.iloc[i:i + 4]
        rows.append((part["bar_end"].iloc[-1], float(part["open"].iloc[0]), float(part["high"].max()), float(part["low"].min()),
                     float(part["close"].iloc[-1])))
    return rows


def _candles(key: str, scale: float) -> list[MdCandleRecord]:
    out = [MdCandleRecord(instrument_key=key, timeframe="15m", ts=b.bar_end.to_pydatetime(), source="vendor:test", version=1,
                          open=float(b.open) * scale, high=float(b.high) * scale, low=float(b.low) * scale, close=float(b.close) * scale,
                          volume=0, ingested_at=b.bar_end.to_pydatetime()) for b in F.itertuples()]
    out += [MdCandleRecord(instrument_key=key, timeframe="1h", ts=end.to_pydatetime(), source="vendor:test", version=1, open=o * scale,
                           high=h * scale, low=lo * scale, close=c * scale, volume=0, ingested_at=end.to_pydatetime())
            for end, o, h, lo, c in _hourly()]
    return out


async def _put_pair() -> None:
    async with _session_factory() as session:
        session.add_all(_candles(PAIR_KEY, 2.0))
        session.add_all([c for c in _candles(f"NSE:{SYMBOL}", 1.0) if c.timeframe == "1h"])
        await session.commit()


@pytest.fixture(scope="module", autouse=True)
def _pair_lake():
    _run(_drop_pair())
    _run(_put_pair())
    yield
    _run(_drop_pair())


def _set_reader_settings(**values) -> None:
    async def go():
        async with _session_factory() as session:
            await controls._set(session, settings_mod.KEY, settings_mod.validate_update(settings_mod.merge({}), values), None)
            await session.commit()
    _run(go())


@pytest.fixture(autouse=True)
def _reader(monkeypatch):
    store: dict[str, str] = {}

    async def get(key):
        return store.get(key)

    async def put(key, value, ttl):
        store[key] = value
    monkeypatch.setattr(cache_client, "cache_get", get)
    monkeypatch.setattr(cache_client, "cache_set", put)
    monkeypatch.setenv("CHART_READ_API_KEY", "test-key-not-real")
    fake_client = FakeClient()
    monkeypatch.setattr(service, "make_client", lambda key: fake_client)
    reader = FakeReader()
    loader.set_for_tests(reader)
    _set_reader_settings(timeframes=["15m", "1h"], pair={SYMBOL: PAIR_SYMBOL})
    yield {"reader": reader, "client": fake_client, "cache": store}
    loader.reset_for_tests()
    _set_reader_settings()


def _user(tag: str, *, on: bool = True) -> dict:
    who = _owner(f"m86-{tag}")
    _allow("chart_price_action", who["tenant_id"])
    if on:
        _allow(FLAG, who["tenant_id"])
    return who


def _read(who: dict, **body):
    return client.post(f"{BASE}/read", headers=who["headers"], json={"strategy": "ABHI", "source": "lake", "symbol": SYMBOL, **body})


def _rows(user_id: int) -> list[LlmCallRecord]:
    async def go():
        async with _session_factory() as session:
            return list((await session.scalars(select(LlmCallRecord).where(LlmCallRecord.user_id == user_id))).all())
    return _run(go())


# --- the flag -------------------------------------------------------------------------------------------------------------

def test_flag_registered_off_by_default_in_every_list():
    assert FLAG in controls.FEATURE_FLAGS and FLAG not in controls.DEFAULT_ON_FLAGS
    assert FLAG in flag_catalog.FLAG_INFO and FLAG in flag_state.FLAG_STATUS
    assert FLAG in controls.CHART_READER_FLAGS


def test_flag_off_answers_503(monkeypatch):
    who = _user("off", on=False)
    for got in (client.get(f"{BASE}/strategies", headers=who["headers"]), _read(who)):
        assert got.status_code == 503 and got.headers.get("X-Feature-Disabled") == FLAG


# --- the loader -----------------------------------------------------------------------------------------------------------

def test_package_absent_gives_the_plain_reason(monkeypatch):
    loader.reset_for_tests()
    monkeypatch.setitem(sys.modules, loader.MODULE, None)        # "not installed" without touching the real import path
    assert loader.load() is None and loader.reason() == loader.REASON_ABSENT
    assert controls.engine_missing_flags()[FLAG] == controls.CHART_READER_MISSING_REASON
    who = _user("absent")
    listed = client.get(f"{BASE}/strategies", headers=who["headers"]).json()
    assert listed["installed"] is False and listed["available"] is False and listed["strategies"] == []
    got = _read(who)
    assert got.status_code == 503 and got.json()["detail"]["code"] == "reader_not_installed"
    assert got.json()["detail"]["reason"] == loader.REASON_ABSENT


def test_loader_logs_name_and_version_only(monkeypatch, caplog):
    loader.reset_for_tests()
    module = types.ModuleType(loader.MODULE)
    module.get_reader = FakeReader                                   # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, loader.MODULE, module)
    with caplog.at_level(logging.INFO, logger=loader.__name__):
        reader = loader.load()
    assert isinstance(reader, FakeReader) and loader.version() == FakeReader.version
    assert "Chart reader loaded: chart_reader fake_read_v1+fake_book_v1" in caplog.text


def test_only_the_loader_names_the_private_package():
    folder = Path(service.__file__).parent
    for path in folder.glob("*.py"):
        assert "atp_private" not in path.read_text(encoding="utf-8") or path.name == "loader.py", path.name


# --- the strategies list --------------------------------------------------------------------------------------------------

def test_strategies_labels_estimate_and_cap_off_by_default():
    who = _user("list")
    got = client.get(f"{BASE}/strategies", headers=who["headers"])
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["available"] is True and body["default"] == "ABHI" and [s["id"] for s in body["strategies"]] == ["ABHI", "BOOK_ONE", "BOOK_TWO"]
    assert body["model"] == "claude-opus-5-5" and body["effort"] == "high"
    assert body["estimate_usd"] == service.estimate_usd(settings_mod.merge({}), 2) > 0
    assert body["daily_cap_usd"] is None and body["spent_today_usd"] == 0.0           # the cap is OFF by default


# --- the lake path ----------------------------------------------------------------------------------------------------------

def test_lake_path_renders_each_timeframe_and_passes_facts(_reader, monkeypatch):
    close = _zones(monkeypatch)
    who = _user("lake")
    got = _read(who)
    assert got.status_code == 200, got.text
    call = _reader["reader"].calls[-1]
    assert len(call["charts"]) == 2 and all(c[2] == "image/png" and c[1][:8] == b"\x89PNG\r\n\x1a\n" for c in call["charts"])
    assert [c[0] for c in call["charts"]] == [f"{SYMBOL} 15-minute", f"{SYMBOL} 1-hour"]
    assert call["facts"].startswith(f"Chart: {SYMBOL} 15-minute") and "Last close:" in call["facts"] and "Area of interest" in call["facts"]
    assert f"{close:.2f}" in call["facts"] or "Last close: " in call["facts"]
    assert call["model"] == "claude-opus-5-5" and call["effort"] == "high" and call["strategy"] == "ABHI"
    body = got.json()
    assert body["label"] == service.LABEL_LAKE and body["source"] == "lake" and body["cached"] is False and body["pair_attached"] is False
    assert body["cost_usd"] == round((5000 * 4.0 + 1500 * 20.0 + 2000 * 0.2) / 1e6, 6)


def test_result_shape_passes_through_unchanged(_reader):
    who = _user("shape")
    got = _read(who, strategy="BOOK_ONE").json()
    expected = FakeReader().read(instrument=SYMBOL, charts=[("a", b"", "image/png")] * 2, facts="", strategy="BOOK_ONE",
                                 model="claude-opus-5-5", effort="high", client=FakeClient())
    for key, value in expected.items():
        assert got[key] == value, key
    assert set(got) == set(expected) | {"cached", "estimate_usd", "cost_usd", "label", "source", "charts", "pair_attached"}


# --- W2: the paired chart ---------------------------------------------------------------------------------------------------

def test_pair_attached_only_for_two_chart_strategies(_reader):
    who = _user("pair")
    one = _read(who, strategy="BOOK_ONE").json()
    assert one["pair_attached"] is False and len(_reader["reader"].calls[-1]["charts"]) == 2
    two = _read(who, strategy="BOOK_TWO").json()
    charts = _reader["reader"].calls[-1]["charts"]
    assert two["pair_attached"] is True and len(charts) == 3
    assert charts[2][0] == f"{PAIR_SYMBOL} 15-minute (paired chart, same bars)"           # the main timeframe
    assert f"Chart: {PAIR_SYMBOL} 15-minute (paired chart, same bars)" in _reader["reader"].calls[-1]["facts"]
    assert two["second_chart_missing"] is False and two["two_chart_conditions"] == ["the paired average confirms the move"]


def test_no_pair_in_settings_keeps_the_readers_second_chart_missing(_reader):
    _set_reader_settings(timeframes=["15m"], pair={})
    who = _user("nopair")
    got = _read(who, strategy="BOOK_TWO").json()
    assert got["pair_attached"] is False and len(_reader["reader"].calls[-1]["charts"]) == 1
    assert got["second_chart_missing"] is True                                          # the reader's own value


# --- the upload path --------------------------------------------------------------------------------------------------------

def test_upload_path_passes_no_facts_and_only_the_owners_item(_reader):
    owner = _user("up")
    other = _user("up-other")
    item = _ca_upload(owner)
    assert item.status_code == 201, item.text
    item_id = item.json()["id"]
    assert client.post(f"{BASE}/read", headers=other["headers"], json={"strategy": "ABHI", "source": "upload", "item_id": item_id}).status_code == 404
    got = client.post(f"{BASE}/read", headers=owner["headers"], json={"strategy": "ABHI", "source": "upload", "item_id": item_id})
    assert got.status_code == 200, got.text
    call = _reader["reader"].calls[-1]
    assert call["facts"] == "" and len(call["charts"]) == 1 and call["instrument"] == service.UPLOAD_INSTRUMENT
    assert got.json()["label"] == service.LABEL_UPLOAD == "AI only"


# --- the cache --------------------------------------------------------------------------------------------------------------

def test_cache_hit_makes_no_call_and_costs_nothing(_reader):
    who = _user("cache")
    first = _read(who).json()
    calls = len(_reader["reader"].calls)
    second = _read(who).json()
    assert first["cached"] is False and second["cached"] is True and second["cost_usd"] == 0.0
    assert len(_reader["reader"].calls) == calls and len(_rows(who["user_id"])) == 1
    assert second["read"] == first["read"]
    other_strategy = _read(who, strategy="BOOK_ONE").json()
    assert other_strategy["cached"] is False and len(_reader["reader"].calls) == calls + 1


def test_structure_fingerprint_ignores_a_new_bar_but_not_a_new_break():
    reading = {"symbol": "X", "timeframe": "15m", "table": [{"tf": "15m", "ready": True, "trend": "up", "weak": False, "protected": 10.0,
               "last_break": None}], "areas": [{"where": "below", "low": 9.0, "high": 9.5, "state": "fresh", "timeframe": "15m",
               "distance": 1.0}], "levels": [], "protected": 10.0, "last_break": None, "trigger_candle": None, "bars": [{"t": 1}]}
    moved = {**reading, "bars": [{"t": 1}, {"t": 2}], "areas": [{**reading["areas"][0], "distance": 1.5}]}
    broke = {**reading, "last_break": {"label": "Swing break", "price": 11.0, "direction": "up", "time": 5}}
    assert service.structure_fingerprint(reading) == service.structure_fingerprint(moved) != service.structure_fingerprint(broke)


# --- the cost cap -----------------------------------------------------------------------------------------------------------

def test_cap_refuses_when_reached(_reader):
    who = _user("cap")
    assert client.put("/api/me/prefs/chart_reader", headers=who["headers"], json={"chart_reader_daily_cap_usd": 0.01}).status_code == 200
    got = _read(who)
    assert got.status_code == 429 and got.json()["detail"]["code"] == "daily_cap_reached"
    assert _reader["reader"].calls == [] and _rows(who["user_id"]) == []
    assert client.put("/api/me/prefs/chart_reader", headers=who["headers"], json={"chart_reader_daily_cap_usd": 1.0}).status_code == 200
    assert _read(who).status_code == 200
    for bad in ({"chart_reader_daily_cap_usd": 0}, {"chart_reader_daily_cap_usd": -1}, {"other": 1}):
        assert client.put("/api/me/prefs/chart_reader", headers=who["headers"], json=bad).status_code == 422, bad


# --- the usage row ----------------------------------------------------------------------------------------------------------

def test_llm_calls_row_holds_placeholders_never_the_private_text(_reader, caplog):
    who = _user("row")
    with caplog.at_level(logging.DEBUG):
        assert _read(who).status_code == 200
    assert _reader["client"].sent and _reader["client"].sent[-1]["system"] == FAKE_PROMPT       # the fake did "send" it
    rows = _rows(who["user_id"])
    assert len(rows) == 1
    row = rows[0]
    placeholder_sha = hashlib.sha256(service.PLACEHOLDER.encode()).hexdigest()
    assert row.feature == "chart_reader" and row.provider == "anthropic" and row.model == "claude-opus-5-5" and row.status == "ok"
    assert row.prompt_version == "fake_read_v1" and row.tenant_id == who["tenant_id"]
    assert row.system_text == row.user_text == row.response_text == service.PLACEHOLDER
    assert row.system_sha256 == row.user_sha256 == row.response_sha256 == placeholder_sha
    assert row.input_tokens == 7000 and row.output_tokens == 1500 and row.cost_usd > 0
    for text in (FAKE_PROMPT, FAKE_ANSWER):
        assert text not in caplog.text
        assert text not in json.dumps([row.system_text, row.user_text, row.response_text, row.status])


def test_a_failed_read_writes_an_error_row_and_a_plain_line(_reader, caplog):
    loader.set_for_tests(FakeReader(raises=RuntimeError(FAKE_PROMPT)))
    who = _user("fail")
    with caplog.at_level(logging.DEBUG):
        got = _read(who)
    assert got.status_code == 502 and got.json()["detail"]["message"] == service.FAILED
    rows = _rows(who["user_id"])
    assert len(rows) == 1 and rows[0].status == "error: RuntimeError" and rows[0].cost_usd == 0.0
    assert FAKE_PROMPT not in caplog.text and FAKE_PROMPT not in got.text


# --- refusals ---------------------------------------------------------------------------------------------------------------

def test_missing_key_is_a_plain_not_connected_refusal(_reader, monkeypatch):
    monkeypatch.delenv("CHART_READ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    who = _user("nokey")
    got = _read(who)
    assert got.status_code == 503 and got.json()["detail"] == {"message": service.NOT_CONNECTED, "code": "not_connected"}
    assert client.get(f"{BASE}/strategies", headers=who["headers"]).json()["connected"] is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback-not-real")                      # the fallback name works
    assert _read(who).status_code == 200


def test_unknown_strategy_and_bad_bodies(_reader):
    who = _user("bad")
    assert _read(who, strategy="NOPE").status_code == 422
    assert _read(who, symbol=None).status_code == 422
    assert client.post(f"{BASE}/read", headers=who["headers"], json={"strategy": "ABHI", "source": "upload"}).status_code == 422
    assert client.post(f"{BASE}/read", headers=who["headers"], json={"strategy": "ABHI", "source": "web"}).status_code == 422


# --- settings ---------------------------------------------------------------------------------------------------------------

def test_settings_defaults_and_validation():
    d = settings_mod.merge({"model": "BAD MODEL", "effort": "huge", "timeframes": ["60min"], "cache_hours": -1})
    assert d == settings_mod.DEFAULTS                                                  # malformed stored values read as defaults
    assert d["timeframes"] == ["1h", "15m"] and d["api_key_env"] == "CHART_READ_API_KEY" and d["api_key_fallback_env"] == "ANTHROPIC_API_KEY"
    for bad in ({"effort": "huge"}, {"api_key_env": "sk-ant-secret"}, {"timeframes": ["1h", "1h"]}, {"pair": {"A": "A"}},
                {"cache_hours": 10_000}, {"unknown": 1}):
        with pytest.raises(settings_mod.SettingsError):
            settings_mod.validate_update(d, bad)
    assert settings_mod.validate_update(d, {"effort": "max", "pair": {"nifty 50": "nifty bank"}})["pair"] == {"NIFTY 50": "NIFTY BANK"}
    assert service.pair_for(d, "NIFTY") == "NIFTY BANK" and service.pair_for(d, "BANKNIFTY") == "NIFTY 50"

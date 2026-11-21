"""SRV2 P2 + P4 ("Pending job purn kr" 21 Nov): the owner's bot settings per symbol and the bot's positions, read-only, on a test
double of the private package.

- Only the configured owner reads or writes them; another admin gets 403 and the refusal is audited.
- The page shows the private field list with his stored value, else Trade's default; strike fields are shown in strikes.
- A save goes through the private `validate`; only the keys he sent are stored (in points for strike fields); errors store nothing.
- Unknown keys are refused; signal symbols go through Trade's own cleaner.
- Positions are read-only, known fields only, IST offsets; an unreadable store gives none."""
import asyncio
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import AuditLogRecord
from app.platform import live_owner
from app.platform import routes as R
from app.srv2bot import loader, settings_page, store
from tests.test_auth_api import _session_factory


def _run(coro):
    return asyncio.run(coro)


class FakeSettings:
    STRATEGY_KEY = "test_strategy"
    SIGNAL_SYMBOLS_CHOICES = ("NIFTY", "BANKNIFTY")
    SIGNAL_SYMBOLS_DEFAULT = ("NIFTY",)
    STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100}
    DEFAULTS = {"enabled_flag": True, "depth_points": 100, "count": 2, "hidden": 7, "itm_points": 200}
    FIELDS = [
        {"key": "enabled_flag", "label": "On", "group": "entry", "type": "bool"},
        {"key": "depth_points", "label": "Depth", "group": "entry", "type": "int", "strikes": True, "unit": "strikes"},
        {"key": "itm_points", "label": "From ATM", "group": "entry", "type": "int", "strikes": -1, "min": -10, "max": 5},
        {"key": "count", "label": "Count", "group": "exit", "type": "int"},
    ]
    seen: list = []

    def merged(self, symbol, stored=None):
        out = dict(self.DEFAULTS)
        out.update(stored or {})
        return out

    def validate(self, symbol, values):
        self.seen.append((symbol, dict(values)))
        errors = {k: "Must be 1 or more." for k, v in values.items() if k == "count" and v < 1}
        return ({} if errors else dict(values)), errors

    def clean_signal_symbols(self, value):
        out = [str(x).upper() for x in value]
        if not out or any(x not in self.SIGNAL_SYMBOLS_CHOICES for x in out):
            raise ValueError("signal_symbols: one of NIFTY, BANKNIFTY")
        return out


class FakePositions:
    def __init__(self, rows=None, broken=False):
        self.rows, self.broken = rows or [], broken

    def read(self, limit=50):
        if self.broken:
            raise OSError("store unreadable")
        return self.rows[:limit]


def _bot(settings=None, positions=None):
    return NS(host=NS(), runner=NS(), modes=None, settings=settings, positions=positions)


@pytest.fixture(autouse=True)
def _fresh():
    loader.set_for_tests(loader._UNSET)
    FakeSettings.seen = []
    yield
    loader.set_for_tests(loader._UNSET)


def _owner(monkeypatch, uid=515151):
    monkeypatch.setattr(live_owner, "is_owner", lambda user: getattr(user, "id", None) == uid)
    return NS(id=uid)


async def _events(event):
    async with _session_factory() as s:
        return list((await s.scalars(select(AuditLogRecord).where(AuditLogRecord.event == event))).all())


def test_only_the_owner_reads_or_writes_and_a_refusal_is_audited(monkeypatch):
    loader.set_for_tests(_bot(FakeSettings(), FakePositions()))
    _owner(monkeypatch)
    other = NS(id=9)

    async def go():
        async with _session_factory() as s:
            for call in (lambda: R.get_srv2_settings("NIFTY", other, s),
                         lambda: R.put_srv2_settings("NIFTY", R.Srv2SettingsBody(values={"count": 3}), other, s),
                         lambda: R.put_srv2_signal_symbols(R.Srv2SignalSymbolsBody(symbols=["NIFTY"]), other, s),
                         lambda: R.get_srv2_positions(other, s)):
                with pytest.raises(HTTPException) as exc:
                    await call()
                assert exc.value.status_code == 403
    _run(go())
    assert FakeSettings.seen == [] and len(_run(_events(settings_page.EVENT + ".refused"))) >= 4


def test_the_page_shows_his_value_else_the_default_and_strikes_in_strikes(monkeypatch):
    loader.set_for_tests(_bot(FakeSettings()))
    me = _owner(monkeypatch)

    async def go():
        async with _session_factory() as s:
            await store.save_row(s, FakeSettings.STRATEGY_KEY, "BANKNIFTY", {"count": 4})
            await s.commit()
            page = await R.get_srv2_settings("banknifty", me, s)
            by = {f["key"]: f for f in page["fields"]}
            assert set(by) == {"enabled_flag", "depth_points", "count", "itm_points"}  # "hidden" is not a page field
            assert by["itm_points"]["value"] == -2 and by["itm_points"]["min"] == -10     # points = -strikes x step: 200 -> -2
            assert by["count"]["value"] == 4 and by["count"]["saved"] is True
            assert by["depth_points"]["value"] == 1 and by["depth_points"]["saved"] is False   # 100 points / 100 step
            assert page["signal_symbols"] == ["NIFTY"] and page["symbols"] == ["NIFTY", "BANKNIFTY"]
            with pytest.raises(HTTPException) as exc:
                await R.get_srv2_settings("XYZ", me, s)
            assert exc.value.status_code == 400
    _run(go())


def test_a_save_validates_stores_only_his_keys_in_points_and_is_audited(monkeypatch):
    loader.set_for_tests(_bot(FakeSettings()))
    me = _owner(monkeypatch)

    async def go():
        async with _session_factory() as s:
            with pytest.raises(HTTPException) as exc:
                await R.put_srv2_settings("NIFTY", R.Srv2SettingsBody(values={"count": 0}), me, s)
            assert exc.value.status_code == 422 and exc.value.detail["errors"] == {"count": "Must be 1 or more."}
            assert await store.get_row(s, FakeSettings.STRATEGY_KEY, "NIFTY") is None          # nothing stored on an error
            with pytest.raises(HTTPException) as exc:
                await R.put_srv2_settings("NIFTY", R.Srv2SettingsBody(values={"hidden": 1}), me, s)
            assert exc.value.status_code == 422
            page = await R.put_srv2_settings("NIFTY", R.Srv2SettingsBody(values={"depth_points": 3}), me, s)
            assert await store.get_row(s, FakeSettings.STRATEGY_KEY, "NIFTY") == {"depth_points": 150}   # 3 strikes x 50
            assert {f["key"]: f["value"] for f in page["fields"]}["depth_points"] == 3
    _run(go())
    assert FakeSettings.seen[-1] == ("NIFTY", {"depth_points": 150})
    assert _run(_events(settings_page.EVENT))


def test_signal_symbols_go_through_trades_cleaner(monkeypatch):
    loader.set_for_tests(_bot(FakeSettings()))
    me = _owner(monkeypatch)

    async def go():
        async with _session_factory() as s:
            out = await R.put_srv2_signal_symbols(R.Srv2SignalSymbolsBody(symbols=["nifty", "banknifty"]), me, s)
            assert out == {"signal_symbols": ["NIFTY", "BANKNIFTY"]}
            with pytest.raises(HTTPException) as exc:
                await R.put_srv2_signal_symbols(R.Srv2SignalSymbolsBody(symbols=["XYZ"]), me, s)
            assert exc.value.status_code == 400
            assert (await R.get_srv2_settings("NIFTY", me, s))["signal_symbols"] == ["NIFTY", "BANKNIFTY"]
    _run(go())


def test_positions_are_read_only_known_fields_with_ist_and_a_broken_store_gives_none(monkeypatch):
    me = _owner(monkeypatch)
    row = {"id": 1, "symbol": "NIFTY", "direction": "bullish", "status": "OPEN", "opened_at": "2026-11-21 09:45:00", "closed_at": None,
           "legs": [{"strike": 24000, "option_type": "PE", "side": "SELL", "qty": 75, "entry_price": 80.5, "exit_price": None,
                     "token": "x"}], "pnl": 0.0, "exit_reason": None, "mode": "PAPER", "internal": "x"}
    loader.set_for_tests(_bot(FakeSettings(), FakePositions([row])))

    async def go():
        async with _session_factory() as s:
            got = await R.get_srv2_positions(me, s)
            p = got["positions"][0]
            assert "internal" not in p and "token" not in p["legs"][0]
            assert p["opened_at"] == "2026-11-21T09:45:00+05:30" and p["closed_at"] is None
    _run(go())
    loader.set_for_tests(_bot(FakeSettings(), FakePositions(broken=True)))
    assert _run(settings_page.positions()) == {"installed": True, "positions": []}
    loader.set_for_tests(_bot(FakeSettings(), None))
    assert _run(settings_page.positions()) == {"installed": False, "positions": []}


def test_an_absent_package_shows_nothing_and_a_save_answers_409(monkeypatch):
    me = _owner(monkeypatch)
    loader.set_for_tests(None)

    async def go():
        async with _session_factory() as s:
            assert (await R.get_srv2_settings("NIFTY", me, s))["installed"] is False
            with pytest.raises(HTTPException) as exc:
                await R.put_srv2_settings("NIFTY", R.Srv2SettingsBody(values={"count": 2}), me, s)
            assert exc.value.status_code == 409
    _run(go())


def test_values_for_page_is_used_when_the_package_has_it_and_the_strike_sign_round_trips(monkeypatch):
    class WithPage(FakeSettings):
        def values_for_page(self, symbol, stored=None):
            return {**self.merged(symbol, stored), "count": 99}
    loader.set_for_tests(_bot(WithPage()))
    me = _owner(monkeypatch)

    async def go():
        async with _session_factory() as s:
            assert {f["key"]: f["value"] for f in (await R.get_srv2_settings("NIFTY", me, s))["fields"]}["count"] == 99
            await R.put_srv2_settings("NIFTY", R.Srv2SettingsBody(values={"itm_points": 3}), me, s)      # 3 strikes OTM
            assert (await store.get_row(s, FakeSettings.STRATEGY_KEY, "NIFTY"))["itm_points"] == -150    # = -3 x 50
            page = await R.get_srv2_settings("NIFTY", me, s)
            assert {f["key"]: f["value"] for f in page["fields"]}["itm_points"] == 3
    _run(go())


def test_srv2_q8_the_stored_row_reaches_a_validate_that_takes_it(monkeypatch):
    seen = []

    class WithStored(FakeSettings):
        def validate(self, symbol, values, stored=None):
            seen.append(dict(stored or {}))
            return dict(values), {}
    loader.set_for_tests(_bot(WithStored()))
    me = _owner(monkeypatch)

    async def go():
        async with _session_factory() as s:
            await store.save_row(s, FakeSettings.STRATEGY_KEY, "NIFTY", {"count": 4})
            await s.commit()
            await R.put_srv2_settings("NIFTY", R.Srv2SettingsBody(values={"count": 5}), me, s)
    _run(go())
    assert len(seen) == 1 and seen[0]["count"] == 4                 # the stored row; an older two-argument validate still works

"""M66 EN-1: the generic boundary to the private package's signal engine (app/engine_provider).

- the loader: the package absent => unavailable with a plain reason, never a crash; each refusal (no get_provider, a
  failing factory, a broken import, the wrong interface, an object without the interface) => unavailable + reason;
- a TEST DOUBLE module put into sys.modules inside the test only (generic made-up values) => available, with the
  version, the tag and the interface number;
- the flag `trade_engine`: registered, OFF by default, not in the trader pack, unavailable to users without the engine;
- the state on the admin System view (admin only) and never on the public /api/system/status;
- `scripts/trade_engine_state.py` and `deploy/atp.sh health` ("trade engine: OK / MISSING / not reported");
- the contract refuses what breaks it (naive times, a bar after as_of, a bad price range);
- only loader.py names the private package, and nothing in the package touches an order path.
"""
import ast
import json
import subprocess
import sys
import types
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from app.engine_provider import contract as c
from app.engine_provider import loader
from app.platform import controls, trader_pack
from app.platform.flag_catalog import FLAG_GROUP, FLAG_INFO, fate_for
from tests.test_admin_api import _admin
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _register, client

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE = BACKEND / "app" / "engine_provider"
T0 = datetime(2024, 1, 2, 3, 45, tzinfo=timezone.utc)


# --- a test double: generic made-up values, no method content -----------------------------------------------------------

class FakeEngine:
    """A test double of the private engine. It returns an empty result; nothing here is a reading rule."""
    name = "test-double-engine"
    version = "0.0.0-test"
    interface = c.INTERFACE_VERSION
    timeframes = ("5m", "1d")

    def read(self, request: c.EngineRequest) -> c.EngineResult:
        return c.EngineResult(interface=c.INTERFACE_VERSION, version=self.version)


def _fake_module(monkeypatch, **attrs) -> types.ModuleType:
    """Put a made-up `atp_private.trade_engine` into sys.modules for this test only."""
    parent = types.ModuleType("atp_private")
    parent.__path__ = []                                    # a package
    module = types.ModuleType("atp_private.trade_engine")
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, "atp_private", parent)
    monkeypatch.setitem(sys.modules, "atp_private.trade_engine", module)
    return module


@pytest.fixture(autouse=True)
def _fresh_loader():
    loader.reset_for_tests()
    yield
    loader.reset_for_tests()


# --- the loader ------------------------------------------------------------------------------------------------------------

def test_absent_package_means_unavailable_with_a_reason_and_no_crash():
    assert loader.load() is None and loader.available() is False
    assert loader.reason() == loader.REASON_ABSENT
    assert loader.describe() == {"installed": False, "message": loader.NOT_INSTALLED, "interface_supported": c.INTERFACE_VERSION}
    admin = loader.describe(admin=True)
    assert admin["reason"] == loader.REASON_ABSENT and admin["owner_steps"] == loader.OWNER_STEPS
    assert loader.tag() == ""


def test_a_test_double_module_loads_with_its_version_tag_and_interface(monkeypatch):
    _fake_module(monkeypatch, get_provider=FakeEngine, TAG="test-tag-1")
    monkeypatch.setenv("ATP_PRIVATE_VERSION", "abc123abc123")
    engine = loader.load()
    assert isinstance(engine, c.TradeEngineProvider) and loader.available()
    assert loader.load() is engine                           # imported once per process
    assert loader.describe(admin=True) == {"installed": True, "name": "test-double-engine", "version": "0.0.0-test",
                                           "tag": "test-tag-1", "interface": c.INTERFACE_VERSION, "checkout": "abc123abc123"}


def test_the_providers_own_tag_wins_and_unprintable_text_is_cleaned(monkeypatch):
    class Tagged(FakeEngine):
        tag = "own-tag\n\x1b[31m"
        version = "1" * 200
    _fake_module(monkeypatch, get_provider=Tagged, __version__="module-version")
    state = loader.describe()
    assert state["tag"] == "own-tag[31m" and len(state["version"]) == loader.TEXT_MAX


def test_the_module_version_is_the_tag_when_nothing_else_names_one(monkeypatch):
    _fake_module(monkeypatch, get_provider=FakeEngine, __version__="9.9.9-test")
    assert loader.describe()["tag"] == "9.9.9-test"


def _boom():
    raise RuntimeError("made-up failure text that must not be shown")


class _WrongInterface(FakeEngine):
    interface = c.INTERFACE_VERSION + 1


@pytest.mark.parametrize("attrs, reason", [
    ({}, "the private package has no get_provider()"),
    ({"get_provider": "not callable"}, "the private package has no get_provider()"),
    ({"get_provider": _boom}, "the engine failed to start (RuntimeError)"),
    ({"get_provider": object}, "the engine does not match ATP's engine interface"),
    ({"get_provider": _WrongInterface}, f"the engine implements interface {c.INTERFACE_VERSION + 1}, this release supports {c.INTERFACE_VERSION}"),
])
def test_each_refusal_is_unavailable_with_its_reason(monkeypatch, attrs, reason):
    _fake_module(monkeypatch, **attrs)
    assert loader.load() is None
    state = loader.describe(admin=True)
    assert state["installed"] is False and state["reason"] == reason
    assert "made-up failure text" not in json.dumps(state)


@pytest.mark.parametrize("exc, reason", [
    (ModuleNotFoundError("No module named 'atp_private'", name="atp_private"), loader.REASON_ABSENT),
    (ModuleNotFoundError("No module named 'somedep'", name="somedep"), "the private package could not be imported (ModuleNotFoundError: somedep)"),
    (SyntaxError("made-up failure text"), "the private package failed while importing (SyntaxError)"),
])
def test_a_broken_import_is_unavailable_never_a_crash(monkeypatch, exc, reason):
    def fake_import(name):
        raise exc
    monkeypatch.setattr(loader.importlib, "import_module", fake_import)
    assert loader.load() is None and loader.reason() == reason


def test_set_for_tests_overrides_the_import():
    loader.set_for_tests(FakeEngine())
    assert loader.describe()["installed"] is True and loader.describe()["tag"] == ""
    loader.set_for_tests(None)
    assert loader.describe()["installed"] is False


# --- the flag --------------------------------------------------------------------------------------------------------------

def test_the_flag_is_registered_off_by_default_and_outside_the_trader_pack():
    assert "trade_engine" in controls.FEATURE_FLAGS
    assert "trade_engine" in controls.DEFAULT_OFF_FLAGS and "trade_engine" not in controls.DEFAULT_ON_FLAGS
    assert "needs the private engine package" in controls.FEATURE_FLAGS["trade_engine"]
    assert "admin / owner only" in controls.FEATURE_FLAGS["trade_engine"]
    assert "trade_engine" not in trader_pack.PACK and "trade_engine" in trader_pack.EXCLUDED_REASONS
    assert FLAG_INFO["trade_engine"].status == "unfinished" and FLAG_GROUP["trade_engine"] == "paper"
    assert fate_for("trade_engine")["fate"] == "finish"


def test_without_the_engine_the_flag_is_unavailable_to_users_even_when_on():
    headers = {"Authorization": f"Bearer {_register(f'm66-{uuid.uuid4().hex[:6]}@example.com')}"}
    tenant = client.get("/api/auth/me", headers=headers).json()["tenant_id"]
    assert client.get("/api/system/features", headers=headers).json()["trade_engine"] is False      # OFF by default
    _set_flag("trade_engine", tenant, on=True)
    try:
        loader.set_for_tests(None)
        assert controls.engine_missing_flags()["trade_engine"] == controls.TRADE_ENGINE_MISSING_REASON
        assert client.get("/api/system/features", headers=headers).json()["trade_engine"] is False
        loader.set_for_tests(FakeEngine())
        assert "trade_engine" not in controls.engine_missing_flags()
        assert client.get("/api/system/features", headers=headers).json()["trade_engine"] is True
    finally:
        _set_flag("trade_engine", tenant, on=False)


# --- where the state is shown --------------------------------------------------------------------------------------------

def test_the_admin_system_view_carries_the_engine_state_and_the_public_status_does_not():
    admin, _ = _admin(f"m66-admin-{uuid.uuid4().hex[:6]}@example.com")
    body = client.get("/api/admin/console/system", headers=admin).json()
    assert body["trade_engine"]["installed"] is False and body["trade_engine"]["reason"] == loader.REASON_ABSENT
    loader.set_for_tests(FakeEngine())
    state = client.get("/api/admin/console/system", headers=admin).json()["trade_engine"]
    assert set(state) == {"installed", "name", "version", "tag", "interface", "checkout"} and state["installed"] is True
    trader = {"Authorization": f"Bearer {_register(f'm66-trader-{uuid.uuid4().hex[:6]}@example.com')}"}
    assert client.get("/api/admin/console/system", headers=trader).status_code == 403
    public = client.get("/api/system/status")
    assert "trade_engine" not in public.text and "test-double-engine" not in public.text


def test_the_state_script_prints_one_json_line():
    out = subprocess.run([sys.executable, "scripts/trade_engine_state.py"], cwd=BACKEND, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and json.loads(out.stdout.strip().splitlines()[-1]) == {"installed": False, "reason": loader.REASON_ABSENT}


@pytest.fixture
def host(tmp_path):
    from tests.test_z11_deploy_bundle import Host
    return Host(tmp_path)


@pytest.mark.parametrize("engine, line", [
    ({"installed": True, "name": "test-double-engine", "version": "0.0.0", "tag": "test-tag", "interface": 1},
     "ok    trade engine: OK (test-double-engine 0.0.0 tag test-tag interface 1)"),
    ({"installed": False, "reason": "the private package is not installed on this server"},
     "WARN  trade engine: MISSING (the private package is not installed on this server)"),
    (None, "trade engine: not reported by this release"),
])
def test_health_reports_the_trade_engine_and_keeps_the_chart_engine_line(host, engine, line):
    assert host.run("deploy").returncode == 0
    host.load()
    host.state["trade_engine"] = engine
    host.state["engine"] = {"installed": False, "reason": "the private package is not installed on this server"}
    host.save()
    res = host.run("health")
    assert line in res.stdout, res.stdout
    assert "WARN  chart analysis engine: MISSING (the private package is not installed on this server)" in res.stdout


# --- the contract ----------------------------------------------------------------------------------------------------------

def _bar(i: int, **kw) -> c.Bar:
    start = T0 + timedelta(minutes=5 * i)
    return c.Bar(start=start, end=start + timedelta(minutes=5), open=10, high=12, low=9, close=11, **kw)


def _calendar() -> c.MarketCalendar:
    return c.MarketCalendar(timezone="Asia/Kolkata", regular_open=time(9, 15), regular_close=time(15, 30),
                            holidays=(date(2024, 1, 26),), early_closes=(c.EarlyClose(date(2024, 11, 1), time(19, 15)),))


def test_a_valid_request_builds_and_the_double_reads_it():
    frame = c.Frame("5m", (_bar(0), _bar(1, volume=None)), futures_volume=(100.0, None))
    request = c.EngineRequest(symbol="INDEX-A", as_of=frame.bars[-1].end, frames={"5m": frame}, calendar=_calendar())
    assert FakeEngine().read(request) == c.EngineResult(interface=1, version="0.0.0-test")


@pytest.mark.parametrize("build", [
    lambda: c.Bar(start=T0.replace(tzinfo=None), end=T0 + timedelta(minutes=5), open=1, high=2, low=1, close=2),
    lambda: c.Bar(start=T0, end=T0, open=1, high=2, low=1, close=2),
    lambda: c.Bar(start=T0, end=T0 + timedelta(minutes=5), open=1, high=2, low=1.5, close=2),
    lambda: c.Frame("5m", (_bar(1), _bar(0))),
    lambda: c.Frame("5m", (_bar(0), _bar(1)), futures_volume=(1.0,)),
    lambda: c.EngineRequest(symbol="X", as_of=T0, frames={"5m": c.Frame("5m", (_bar(0),))}, calendar=_calendar()),
    lambda: c.EngineRequest(symbol="X", as_of=T0 + timedelta(hours=1), frames={"15m": c.Frame("5m", (_bar(0),))}, calendar=_calendar()),
    lambda: c.PriceRange(low=2, high=1),
    lambda: c.Area(area_id="a", side="middle", state="s", band=c.PriceRange(1, 2), timeframe="1h", formed_at=T0),  # type: ignore[arg-type]
    lambda: c.Swing(label="XX", at=T0, price=1, timeframe="1h"),  # type: ignore[arg-type]
    lambda: c.HigherTimeframeSide(side="left", timeframe="1d"),  # type: ignore[arg-type]
])
def test_the_contract_refuses_what_breaks_it(build):
    with pytest.raises(c.ContractViolation):
        build()


def test_a_signal_carries_tokens_never_text_and_no_order_field():
    from app.signals.language import SignalStructure
    signal = c.Signal(model_id="model-x", timeframe="15m", bar_end=T0, side="buyer", entry_zone=c.PriceRange(1, 2),
                      card=SignalStructure(trend="up", side="buyer", timeframe="15m"), evidence=(c.Evidence("token-a", True),))
    assert signal.card.unknown() == []
    names = set(c.Signal.__dataclass_fields__)
    assert not names & {"lots", "quantity", "qty", "order_type", "target", "stop_loss", "exit", "text", "message"}


# --- the package stays a boundary ------------------------------------------------------------------------------------------

def test_only_the_loader_names_the_private_package_and_nothing_touches_an_order_path():
    banned = ("app.execution", "app.paper", "app.trading", "app.live", "app.brokers", "app.deployments")
    for path in PACKAGE.glob("*.py"):
        text = path.read_text()
        assert "atp_private" not in text or path.name in ("loader.py", "__init__.py"), path.name
        tree = ast.parse(text)
        imported = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        for name in imported:
            assert not name.startswith(banned), (path.name, name)
        assert "place_order" not in text and "submit_order" not in text, path.name
    imports = [n for n in ast.walk(ast.parse((PACKAGE / "__init__.py").read_text())) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert imports == []                                     # the package docstring names it; only the loader imports it

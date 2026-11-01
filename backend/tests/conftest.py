"""Suite-wide test isolation.

The broker circuit breakers (app/brokers/circuit_breaker.py) are process-wide, time-windowed state:
fake brokers in many worker tests raise NotImplementedError for calls they do not model, and those
failures count towards "3 of 5 calls failed in 60 s". Without a reset, whether a later LIVE test
finds the circuit open depends on how fast the tests before it ran. Each test starts closed.

The global cues (app/ai/global_cues.py) fetch free public market data over the internet; the suite
never does - tests that exercise them turn the flag on and serve the responses themselves.

Feature flags and platform-wide (tenant_id NULL) market events live in the one in-memory database every
test module shares. A module that switches `news_feed` on, or seeds a global RBI event dated today, must
not decide what the next module sees: both are reset after every module. So are positions a module left open - the
worker's exits sweep covers every open position, with or without a deployment.
"""
import os

import pytest

os.environ["GLOBAL_CUES_ENABLED"] = "false"

from app.brokers.circuit_breaker import reset_all


@pytest.fixture(autouse=True)
def _closed_circuit_breakers():
    reset_all()
    from app.platform.routes import forget_status
    forget_status()                     # LD-7: the public status is cached for a few seconds per process; each test reads afresh
    from app.market_data import shared_candles
    shared_candles.reset()              # LD-10: candles one test fetched are never another test's shared copy
    yield


@pytest.fixture(autouse=True)
def _no_instrument_master_download(monkeypatch):
    """P1 (27 Oct): a ticket that misses a contract starts an on-demand master refresh (a real Upstox download). Tests never
    download: the refresh answers as "refreshing"; tests/test_p1_master_sync.py calls the real function with a fake sync."""
    from app.instruments import master_sync

    async def _stub(session_factory, exchanges, now=None):
        return master_sync.status_text({}, refreshing=True)
    monkeypatch.setattr(master_sync, "refresh_on_demand", _stub)
    yield


@pytest.fixture(autouse=True)
def _saved_order_defaults(request):
    """M68 PR-1: ATP pre-chooses no holding, product, order type, band or trigger - each comes from the user's own Trade
    settings > Order defaults (app/execution/order_defaults.py). The tests written before M68 run as a user who SAVED
    Intraday / MIS / Market / 0.5 % / touch / the option's own price there (the values they were written against); a value
    he really saved still wins. Tests of the M68 behaviour itself (Not set => ask / skip) carry the marker
    `real_order_defaults` and read the real, empty settings (tests/test_m68_order_defaults.py)."""
    if request.node.get_closest_marker("real_order_defaults"):
        yield
        return
    from dataclasses import replace

    from app.execution import order_defaults
    real = order_defaults.load
    saved = order_defaults.OrderDefaults(holding="INTRADAY", product="MIS", order_type="MARKET", price_protection_pct=0.5,
                                         trigger_mode="TOUCH", trigger_source="PREMIUM")

    async def _load(session, user):
        own = await real(session, user)
        return replace(saved, **{k: v for k, v in vars(own).items() if v is not None})
    with pytest.MonkeyPatch.context() as own:      # its own patch: a test's monkeypatch.undo() leaves it in place
        own.setattr(order_defaults, "load", _load)
        yield


@pytest.fixture(autouse=True)
def _saved_automation(request):
    """M68 PR-6: ATP pre-chooses no entry mode, request waiting time, re-quote limit or scanner limit - each comes from the
    user's own Trade settings > Automation (app/execution/automation.py). The tests written before M68 run as a user who
    SAVED "Placed automatically" and the operator values they were written against (read at call time, so a test that
    patches app.core.config still gets its value); a value he really saved still wins. Tests of the M68 behaviour itself
    carry the marker `real_automation` and read the real, empty settings (tests/test_m68_automation.py)."""
    if request.node.get_closest_marker("real_automation"):
        yield
        return
    from dataclasses import replace

    from app.core import config as app_config
    from app.execution import automation
    real = automation.load

    async def _load(session, user):
        own = await real(session, user)
        saved = automation.Automation(
            approval_mode="AUTO", approval_entry_minutes=app_config.APPROVAL_ENTRY_TTL_MINUTES,
            requote_drift_pct=app_config.APPROVAL_REQUOTE_MAX_DRIFT_PCT or None, max_requotes=app_config.APPROVAL_MAX_REQUOTES,
            scan_auto_max=app_config.SCAN_AUTO_PAPER_MAX_PER_DAY, scan_auto_cooldown_minutes=app_config.SCAN_AUTO_PAPER_COOLDOWN_MINUTES,
            scan_auto_arming_days=app_config.SCAN_AUTO_PAPER_ARM_DAYS)
        return replace(saved, **{k: v for k, v in vars(own).items() if v is not None})
    with pytest.MonkeyPatch.context() as own:      # its own patch: a test's monkeypatch.undo() leaves it in place
        own.setattr(automation, "load", _load)
        yield


@pytest.fixture(autouse=True)
def _no_broker_margin_call(monkeypatch):
    """DEF-0 D8: the order ticket asks the user's own broker for its margin. Tests never call a broker: the read answers
    "no answer"; tests/test_def0_ui.py stubs an answer to show the estimate."""
    from app.paper import fno_ticket

    async def _none(*_args, **_kw):
        return None
    monkeypatch.setattr(fno_ticket, "read_broker_margin", _none)
    yield


@pytest.fixture(autouse=True, scope="module")
def _platform_state_reset_per_module():
    yield
    import asyncio
    from datetime import datetime, timezone

    from sqlalchemy import delete, update

    from app.db.models import MarketEventRecord, TradeRecord
    from app.journal import capture
    from app.platform import controls
    from tests.test_auth_api import _session_factory

    async def reset():
        async with _session_factory() as session:
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": {}}, None)
            await session.execute(delete(MarketEventRecord).where(MarketEventRecord.tenant_id.is_(None)))
            # The worker sweeps every open position, deployment or not (ADR-0004): a position a module left open would be
            # priced and closed by the next module's worker cycles. Each module starts with none open.
            await session.execute(update(TradeRecord).where(TradeRecord.exit_time.is_(None))
                                  .values(exit_time=datetime.now(timezone.utc), exit_price=TradeRecord.entry_price, pnl=0.0,
                                          exit_reason="test isolation: left open by an earlier module"))
            await session.commit()
    try:
        asyncio.run(reset())
    except Exception:  # noqa: BLE001 - a module that never touched the database has nothing to reset
        pass
    capture.reset_flag_cache()   # Z6: the journal hooks cache the flags they just saw reset
    from app.platform import phase0_lock
    phase0_lock.reset_cache()    # Z10-c: the broker chokepoint reads the lock state the last flag read left


@pytest.fixture
def no_open_positions_from_earlier_tests():
    """For modules whose worker tests count what one cycle closed: the exits sweep covers every open position, deployment
    or not (ADR-0004), so positions an earlier test of the same module left open are closed before this test starts."""
    import asyncio
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.db.models import TradeRecord
    from tests.test_auth_api import _session_factory

    async def close_leftovers():
        async with _session_factory() as session:
            await session.execute(update(TradeRecord).where(TradeRecord.exit_time.is_(None))
                                  .values(exit_time=datetime.now(timezone.utc), exit_price=TradeRecord.entry_price, pnl=0.0,
                                          exit_reason="test isolation: left open by an earlier test"))
            await session.commit()
    asyncio.run(close_leftovers())
    yield


@pytest.fixture
def owner_switched_live_on(monkeypatch):
    """M55 S1: LIVE is OFF by default (live_trading OFF, the phase0_paper_only lock ON, no plan grants LIVE, a USER has
    no trading:live) and only the owner may switch it on. The LIVE path itself is the same code as PAPER and stays
    under test: a module that exercises it uses this fixture for the posture the owner switches on - the defaults from
    before M55 S1 (live_trading ON, the lock OFF, Pro / Business grant LIVE, USER has trading:live). A stored flag
    entry still decides, exactly as on a real host. The new defaults are pinned in tests/test_m55_s1_owner_live.py."""
    from dataclasses import replace

    from app.auth import scopes
    from app.core.enums import UserRole
    from app.plans import registry
    from app.platform import controls

    on = (controls.DEFAULT_ON_FLAGS - {"phase0_paper_only"}) | {"live_trading"}
    monkeypatch.setattr(controls, "DEFAULT_ON_FLAGS", frozenset(on))
    monkeypatch.setattr(controls, "DEFAULT_OFF_FLAGS", frozenset(controls.FEATURE_FLAGS) - frozenset(on))
    for plan_id in ("pro", "business"):
        monkeypatch.setitem(registry.PLANS, plan_id, replace(registry.PLANS[plan_id], live_trading=True))
    # M55 S1b: the plan check needs the owner's per-organisation grant; in this posture a Pro / Business organisation
    # counts as granted (the grant itself is pinned in tests/test_m55_s1b_org_live_grant.py).
    from app.plans import limits

    async def plan_grants(session, tenant):
        return registry.get_plan(tenant.plan).live_trading

    monkeypatch.setattr(limits, "_live_granted", plan_grants)
    user = UserRole.USER.value
    monkeypatch.setitem(scopes.ROLE_SCOPES, user, scopes.ROLE_SCOPES[user] | {"trading:live"})
    yield


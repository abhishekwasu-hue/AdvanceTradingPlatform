"""DATA-FIX (owner 2 Nov, market open): (1) "Read now" said "Your role cannot start a market read." to a SUPER_ADMIN
whom the endpoint itself accepts (require_trader lets SUPER_ADMIN pass); (2) with no daily broker login the worker's
automatic market read stored only the global cues all day - it now reads through the Upstox analytics token (M85)."""
from types import SimpleNamespace

from app.ai import briefing
from app.ai import market_memory as mm
from app.db.models import TraderProfileRecord
from tests.test_auth_api import _session_factory
from tests.test_phase_ar_market_memory import NOW, _add, _Broker, _FakeMD, _run, _tenant


def test_super_admin_is_not_refused_by_role_and_viewer_still_is(monkeypatch):
    from app.market_data import live_access, reader_credential

    async def off(*a, **k):
        return False

    async def reader(*a, **k):
        return SimpleNamespace(id=1)
    monkeypatch.setattr(live_access, "byob_on", off)
    monkeypatch.setattr(reader_credential, "credential_for_reader", reader)
    admin = SimpleNamespace(role="SUPER_ADMIN", tenant_id=1, id=1)
    viewer = SimpleNamespace(role="VIEWER", tenant_id=1, id=2)
    assert _run(briefing.read_now_state(None, admin)) == {"enabled": True, "broker": "ok", "reason": None}
    assert _run(briefing.read_now_state(None, viewer))["broker"] == "role"


def test_worker_market_read_uses_the_analytics_token_when_the_daily_login_ended(monkeypatch):
    from app.workers import trading_worker as tw
    _, tenant_id, user_id = _tenant("datafix-memory@example.com")
    _run(_add(TraderProfileRecord(tenant_id=tenant_id, user_id=user_id, answers_json="{}", preferences_json="{}")))
    worker = tw.TradingWorker(_session_factory, cycle_seconds=60, market_data_factory=_FakeMD)

    class _Account:
        status, broker_name, account_label = "ACTIVE", "upstox", "primary"

    async def accounts(session, tid):
        return [_Account()] if tid == tenant_id else []

    async def no_daily(self, *a, **k):
        return None
    data_only = {"adapter": _Broker()}

    async def analytics(self, session, tid, now):
        return data_only["adapter"]
    monkeypatch.setattr(tw, "list_accounts", accounts)
    monkeypatch.setattr(tw.TradingWorker, "_usable_adapter", no_daily)
    monkeypatch.setattr(tw.TradingWorker, "_data_only_adapter", analytics)

    async def go(now):
        async with _session_factory() as session:
            return await worker._market_memory(session, now)
    assert _run(go(NOW)) >= 2 + len(mm.CUES)            # NIFTY 50 + NIFTY BANK + cues, not the global cues alone


def test_chart_card_says_where_the_price_is_from_the_live_price():
    """D1 (owner 2 Nov): last close 23,053.10 inside the supply 23,020.95-23,162.70; live 23,339 is above it."""
    from app.ai import chart_answer, copilot_chat
    from app.ai.tools import ToolResult
    res = ToolResult(True, {"last_close": 23053.10, "official_close": {"close": 23053.10, "date": "2026-10-30"}}, None, "x")
    zones = ToolResult(True, {"bands": [{"low": 23020.95, "high": 23162.70, "role": "bearish"}]}, None, "x")
    closed = copilot_chat.chart_headline("NIFTY", "1d", res, zones)
    assert closed["area"]["where"] == "inside" and closed["live_price"] is None and closed["price"] == 23053.10
    live = copilot_chat.chart_headline("NIFTY", "1d", res, zones, {"price": 23339.05, "at": "2026-11-02T06:02:00+00:00"})
    assert live["price"] == 23053.10 and live["price_label"] == "Last close"          # the close stays shown
    assert live["live_price"] == 23339.05 and live["live_at"] == "2026-11-02T06:02:00+00:00"
    assert live["area"]["where"] == "below"                                            # the area is below the live price
    eng = chart_answer.engine_headline({"symbol": "NIFTY", "last_close": 23053.10, "timeframe": "1d",
                                        "areas": [{"low": 23020.95, "high": 23162.70, "side": "seller"}]},
                                       {"price": 23339.05, "at": None})
    assert eng["live_price"] == 23339.05 and eng["area"]["where"] == "below"


def test_live_price_is_none_while_the_market_is_closed(monkeypatch):
    from app.ai import chart_answer
    from app.market_data import calendar

    async def closed(*a, **k):
        return SimpleNamespace(is_open=False)
    monkeypatch.setattr(calendar, "market_session_status", closed)
    ctx = SimpleNamespace(session=None, now=None)
    assert _run(chart_answer.live_price(ctx, "NIFTY", "NSE")) is None

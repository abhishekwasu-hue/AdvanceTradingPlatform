"""M71Q-2 (data terms): with `byob_data_tier` OFF, a market-data READ goes only through a credential the reader linked
himself (`credential_for_reader`) - in a two-member organisation member B never triggers a read with member A's
credential, on any page family (charts / last price / option chains / watchlist quotes, the store path, screener,
option-chain analyses + ask, probes, market memory, strategist, backtest bars, the data hub, Greeks, portfolio value,
the deployment preview). A single-member organisation reads exactly as before.
"""
import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.ai import evidence
from app.ai import routes as ai_routes
from app.auth.security import hash_password
from app.brokers import token_lifecycle
from app.db.models import BrokerCredentialRecord, User
from app.deployments import routes as deployment_routes
from app.market_data import candles_routes, candles_store, hub, quotes_routes
from app.market_data import probe as md_probe
from app.market_data.candles_routes import TEAMMATE_BROKER, CandlesBody, ChainsBody
from app.market_data.quotes_routes import QuotesBody
from app.market_data.reader_credential import NO_READER, credential_for_reader, reader_id
from app.market_store import sources
from app.option_chain import analyses_routes, ask_adapter
from app.option_chain import probe as oc_probe
from app.screener import routes as screener_routes
from app.screener import universes
from app.trading import routes as trading_routes
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner

NOW = datetime.now(UTC)


def _run(coro):
    return asyncio.run(coro)


def _member(tenant_id: int, email: str) -> int:
    async def go():
        async with _session_factory() as session:
            user = User(tenant_id=tenant_id, email=email, hashed_password=hash_password("S3cur3Pass!"), role="USER", is_active=True)
            session.add(user)
            await session.commit()
            return user.id
    return _run(go())


def _cred(tenant_id: int, user_id: int, broker: str = "upstox", label: str = "primary") -> int:
    async def go():
        async with _session_factory() as session:
            row = BrokerCredentialRecord(tenant_id=tenant_id, user_id=user_id, broker_name=broker, account_label=label,
                                         encrypted_payload="x", token_status="VALID", token_expires_at=None)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _org(tag: str, *, b_linked: bool = False):
    """(tenant, A, B, A's credential id, B's credential id or None): A linked a broker; B too when `b_linked`."""
    _, a = _owner(f"m71rc-{tag}-a@example.com")
    b = _member(a["tenant_id"], f"m71rc-{tag}-b@example.com")
    a_cred = _cred(a["tenant_id"], a["id"])
    b_cred = _cred(a["tenant_id"], b, broker="zerodha") if b_linked else None
    return a["tenant_id"], a["id"], b, a_cred, b_cred


def _single(tag: str):
    _, a = _owner(f"m71rc-{tag}-solo@example.com")
    return a["tenant_id"], a["id"], _cred(a["tenant_id"], a["id"])


class Used(Exception):
    """Raised by the fake adapter builder: carries the credential a read was about to go through."""

    def __init__(self, record):
        super().__init__(f"read through credential {record.id}")
        self.record = record


@pytest.fixture
def spy(monkeypatch):
    """Every credential pick (`_pick_record`) and every adapter build is recorded; a build stops the read there."""
    picks: list = []
    built: list = []
    real = candles_routes._pick_record

    async def pick(session, tenant_id, broker, label, **kw):
        picks.append(kw.get("user_id"))
        return await real(session, tenant_id, broker, label, **kw)

    def build(record, *a, **k):
        built.append(record.id)
        raise Used(record)
    monkeypatch.setattr(candles_routes, "_pick_record", pick)
    for module in (token_lifecycle, candles_routes, quotes_routes, candles_store, deployment_routes):
        monkeypatch.setattr(module, "build_adapter", build)
    from app.option_chain import futures_routes
    from app.portfolio import routes as portfolio_routes
    monkeypatch.setattr(futures_routes, "build_adapter", build)
    monkeypatch.setattr(portfolio_routes, "build_adapter", build)
    return picks, built


def _as(user_id: int, fn):
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, user_id)
            return await fn(session, user)
    return _run(go())


def _outcome(user_id: int, fn):
    """("used", credential id) | ("refused", status, detail) | ("value", value)."""
    try:
        value = _as(user_id, fn)
    except Used as used:
        return ("used", used.record.id)
    except HTTPException as exc:
        return ("refused", exc.status_code, exc.detail)
    return ("value", value)


# --- the helper --------------------------------------------------------------------------------------------------------

def test_credential_for_reader_two_members_and_single_member():
    tenant, a, b, a_cred, _ = _org("helper")

    async def pick(user_id):
        async with _session_factory() as session:
            return await credential_for_reader(session, tenant, user_id)
    assert _run(pick(a)).id == a_cred                                    # A: his own
    with pytest.raises(HTTPException) as exc:
        _run(pick(b))                                                    # B: never A's
    assert exc.value.status_code == 409 and exc.value.detail == TEAMMATE_BROKER
    with pytest.raises(HTTPException) as none:
        _run(pick(None))
    assert none.value.status_code == 409 and none.value.detail == NO_READER
    b_cred = _cred(tenant, b, broker="zerodha")
    assert _run(pick(b)).id == b_cred                                    # B linked his own: his own
    assert _run(pick(a)).id == a_cred
    solo_tenant, solo, solo_cred = _single("helper")

    async def solo_pick():
        async with _session_factory() as session:
            old = await candles_routes._pick_record(session, solo_tenant, None, "primary")
            new = await credential_for_reader(session, solo_tenant, solo)
            return old.id, new.id
    assert _run(solo_pick()) == (solo_cred, solo_cred)                   # single member: the same pick as before


def test_reader_id_only_inside_the_tenant():
    class U:
        id, tenant_id = 5, 2
    assert reader_id(U(), 2) == 5 and reader_id(U(), 3) is None and reader_id(None, 2) is None


# --- route families: B (no credential of his own) never reads through A's; B with his own reads through his ----------

def _families():
    candles = CandlesBody(symbols=["RELIANCE"], timeframe="5min", lookback_days=2)
    return {
        "charts": lambda s, u: candles_routes.broker_candles(candles, u, s),
        "ltp": lambda s, u: candles_routes.ltp("RELIANCE", "NSE", None, "primary", user=u, session=s),
        "option_chains": lambda s, u: candles_routes.option_chains(ChainsBody(underlyings=["NIFTY"]), user=u, session=s),
        "quotes": lambda s, u: quotes_routes.quotes(QuotesBody(items=[{"symbol": "RELIANCE", "exchange": "NSE"}]), user=u, session=s),
        "candles_probe": lambda s, u: md_probe.run_candles_probe(s, u, "RELIANCE", "NSE", "5min"),
        "chain_probe": lambda s, u: oc_probe.run_chain_probe(s, u, "NIFTY"),
        "screener": lambda s, u: screener_routes.fetch_frames(s, u.tenant_id, ["RELIANCE"], "NSE", "1d", {"1d": 30}, user_id=u.id),
    }


@pytest.mark.parametrize("family", sorted(_families()))
def test_member_b_never_reads_with_member_a_credential(spy, family):
    picks, built = spy
    tenant, a, b, a_cred, _ = _org(f"fam-{family}")
    fn = _families()[family]
    out = _outcome(b, fn)
    assert a_cred not in built, family
    assert out[0] == "refused" and out[1] == 409 and out[2] == TEAMMATE_BROKER, (family, out)
    assert b in picks and None not in picks, family                     # the pick was made for B himself
    built.clear()
    b_cred = _cred(tenant, b, broker="upstox", label="b-own")
    out = _outcome(b, fn)
    assert out == ("used", b_cred), (family, out)                        # his own credential, never A's
    assert a_cred not in built


@pytest.mark.parametrize("family", sorted(_families()))
def test_single_member_reads_as_before(spy, family):
    _, built = spy
    _tenant, solo, solo_cred = _single(f"fam-{family}")
    assert _outcome(solo, _families()[family]) == ("used", solo_cred), family


# --- helpers that answer "nothing" instead of raising --------------------------------------------------------------------

def test_store_path_and_rest_topups_never_use_a_teammates_credential(spy):
    _, built = spy
    tenant, a, b, a_cred, _ = _org("store")

    async def own(session, user):
        out = await candles_store.own_source(session, user, [("RELIANCE", "NSE")], CandlesBody(symbols=["RELIANCE"]), None)
        return out.record
    assert _outcome(b, own) == ("value", None)                           # no session: the store's bars only

    async def rest(session, user):
        return await sources.own_rest(session, user, "RELIANCE", "NSE")
    assert _outcome(b, rest) == ("value", None)

    async def store_rest(session, user):
        return await screener_routes._store_rest(session, tenant, user, ["RELIANCE"], "NSE", None)
    assert _outcome(b, store_rest) == ("value", (None, set()))
    assert a_cred not in built
    assert _outcome(a, own) == ("used", a_cred)                          # A, his own: as before


def test_option_chain_analyses_and_ask_say_connect_your_broker(spy):
    _, built = spy
    tenant, a, b, a_cred, _ = _org("chain")
    for module in (analyses_routes, ask_adapter):
        async def rec(session, user, module=module):
            return await module._record(session, user, "NIFTY")
        got = _outcome(b, rec)
        assert got[0] == "value" and got[1][0] is None and got[1][2], module.__name__
        mine = _outcome(a, rec)
        assert mine[0] == "value" and mine[1][0].id == a_cred
    assert a_cred not in built


def test_strategist_and_market_memory_read_only_the_askers_own(spy, monkeypatch):
    _, built = spy
    tenant, a, b, a_cred, _ = _org("ai")

    async def frames(session, user):
        return await ai_routes._strategist_frames(session, user, ai_routes.StrategistBody(symbol="RELIANCE"))
    out = _outcome(b, frames)
    assert out[0] == "refused" and out[2] == TEAMMATE_BROKER
    assert _outcome(a, frames) == ("used", a_cred)
    built.clear()

    from app.ai import market_memory

    async def no_globals(session, tenant_id):
        return {"globals": []}
    monkeypatch.setattr(market_memory, "capture_global", no_globals)

    async def memory(session, user):
        return await ai_routes.refresh_market_memory(ai_routes.MemoryRefreshBody(), user=user, session=session)
    out = _outcome(b, memory)
    assert out[0] == "refused" and out[2] == TEAMMATE_BROKER
    assert a_cred not in built


def test_backtest_bars_read_only_the_askers_own(spy):
    _, built = spy
    tenant, a, b, a_cred, _ = _org("bt")

    async def frame(session, user):
        from app.backtest import bar_source
        return await bar_source.resolve(session, tenant, "RELIANCE", "NSE", "5min", lookback_days=2, user=user)
    out = _outcome(b, frame)
    assert out[0] == "refused" and out[1] == 409
    assert a_cred not in built
    assert _outcome(a, frame) == ("used", a_cred)

    async def evidence_frame(session, user):                             # no user: the organisation's job, as before
        return await evidence.server_frame(session, tenant, "RELIANCE", "NSE", "5min", lookback_days=2, user=None)
    assert _outcome(a, evidence_frame) == ("used", a_cred)


def test_data_hub_connection_and_feed_only_for_the_credential_owner():
    tenant, a, b, a_cred, _ = _org("hub")
    _set_flag(hub.FLAG, tenant, on=True)

    async def conn(session, user):
        return await hub.user_connection(session, user, NOW)
    assert _outcome(b, conn) == ("value", None)                          # A's stream is never B's
    got = _outcome(a, conn)
    assert got[0] == "value" and got[1] is not None and got[1].record.id == a_cred

    async def feed(session, user):
        return await hub.org_feed(session, tenant, "upstox", [("RELIANCE", "NSE")], reader_id=user.id)
    assert _outcome(b, feed) == ("value", None)
    assert _outcome(a, feed)[1] is not None

    async def legacy(session, user):                                     # no reader named (exit prices): unchanged
        return await hub.org_feed(session, tenant, "upstox", [("RELIANCE", "NSE")])
    assert _outcome(b, legacy)[1] is not None


def test_greeks_portfolio_and_preview_read_only_the_viewers_own(spy):
    _, built = spy
    tenant, a, b, a_cred, _ = _org("pos")

    async def preview(session, user):
        return await deployment_routes._usable_adapter(session, tenant, user=user, symbol="RELIANCE")
    assert _outcome(b, preview) == ("value", None)
    assert _outcome(a, preview) == ("used", a_cred)
    built.clear()

    async def spot(session, user):
        return await deployment_routes._spot_from_broker(session, tenant, "RELIANCE", user=user)
    assert _outcome(b, spot) == ("value", None)
    assert a_cred not in built

    async def greeks(session, user):
        return await trading_routes.open_position_greeks(user=user, session=session)
    assert _outcome(b, greeks)[0] == "value"                             # no open option rows: nothing read, no A
    assert a_cred not in built


def test_screener_batch_plan_names_the_readers_own_broker():
    tenant, a, b, a_cred, _ = _org("batch")
    _cred(tenant, b, broker="zerodha")

    async def name(session, user):
        return await universes._broker_name(session, tenant, user)
    assert _outcome(b, name) == ("value", "zerodha")
    assert _outcome(a, name) == ("value", "upstox")

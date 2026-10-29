"""AUD-G2.2: live prices pushed end to end (flag `live_push`, with `ui_v2` + `data_hub`; OFF by default).

* the stream's `ready` carries the push settings only while the flag is on (byte for byte as before while off);
* auth: no token, no stream; the tenant and user come from the token, never from the request;
* tenant / user isolation: a tab id another user registered carries none of that user's symbols or ticks;
* bounded: at most LIVE_PUSH_MAX_STREAMS_PER_USER push streams per user, the slot given back on every way out;
* heartbeat: a `beat` event with the push, the old comment line without it.
No network: fake feeds, an in-memory cache.
"""
import asyncio
import json
from datetime import datetime, timezone

from app.core import config
from app.live import routes as live_routes
from app.market_data import hub as data_hub
from app.market_data.stream import Tick
from app.platform import controls
from app.platform.flag_catalog import FLAG_INFO
from tests.test_auth_api import _session_factory, client
from tests.test_tm1_data_hub import SID, _clean, _flags, _hub_user  # noqa: F401 - the autouse fixture

UTC = timezone.utc
run = asyncio.run
OTHER_SID = "tabB" + "1" * 12


def _short_stream(monkeypatch, seconds: float = 0.0):
    """The route's stream ends after `seconds` (no wait for the 5-minute end)."""
    monkeypatch.setattr(live_routes, "session_factory", _session_factory)
    real = live_routes.events

    def short(*args, **kwargs):
        return real(*args, **{**kwargs, "max_seconds": seconds, "poll_seconds": 0.02})
    monkeypatch.setattr(live_routes, "events", short)


def _events(body: str):
    out = []
    for block in body.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
        if "event" in lines:
            out.append((lines["event"], json.loads(lines.get("data", "{}"))))
    return out


async def _collect(gen):
    return "".join([chunk.decode() async for chunk in gen])


def test_the_flag_is_registered_off_by_default_and_needs_ui_v2_and_the_data_hub():
    assert "live_push" in controls.FEATURE_FLAGS and "live_push" in controls.DEFAULT_OFF_FLAGS
    assert FLAG_INFO["live_push"].depends_on == ("ui_v2", "data_hub")
    assert config.LIVE_PUSH_STALE_SECONDS > 0 and config.LIVE_PUSH_HEARTBEAT_SECONDS > 0


def test_no_token_no_stream():
    assert client.get(f"/api/live/stream?sid={SID}").status_code == 401


def test_flag_off_ready_is_exactly_as_before_and_flag_on_carries_the_push(monkeypatch):
    _short_stream(monkeypatch)
    headers, me = _hub_user("push-off")
    off = _events(client.get(f"/api/live/stream?sid={SID}", headers=headers).text)
    assert off[0] == ("ready", {"retry_ms": 2000})
    _flags([me["tenant_id"]], live_push=True)
    on = _events(client.get(f"/api/live/stream?sid={SID}", headers=headers).text)
    assert on[0][0] == "ready" and on[0][1]["retry_ms"] == 2000
    assert on[0][1]["push"] == {"stale_after_s": config.LIVE_PUSH_STALE_SECONDS, "beat_s": config.LIVE_PUSH_HEARTBEAT_SECONDS,
                                "bar_settle_s": config.LIVE_PUSH_BAR_SETTLE_SECONDS, "max_symbols": data_hub.BROWSER_SYMBOL_CAP}
    assert live_routes._push_streams == {}                                   # the slot came back when the stream ended
    # without a tab id there is no feed, so no push either (the PW4 reload stream as before)
    assert _events(client.get("/api/live/stream", headers=headers).text)[0] == ("ready", {"retry_ms": 2000})


def test_live_push_without_the_data_hub_gives_no_push(monkeypatch):
    _short_stream(monkeypatch)
    headers, me = _hub_user("push-nohub")
    _flags([me["tenant_id"]], live_push=True, data_hub=False)
    assert _events(client.get(f"/api/live/stream?sid={SID}", headers=headers).text)[0] == ("ready", {"retry_ms": 2000})


def test_another_users_tab_id_carries_none_of_their_ticks(monkeypatch):
    _short_stream(monkeypatch, seconds=0.3)
    headers, me = _hub_user("iso-a")
    other_headers, other = _hub_user("iso-b")
    _flags([me["tenant_id"], other["tenant_id"]], live_push=True)
    assert client.put("/api/live/ticks", headers=headers, json={"sid": SID, "items": [{"symbol": "NIFTY"}]}).status_code == 200
    now = datetime.now(UTC)

    async def tick():
        for tenant, ltp in ((me["tenant_id"], 24000.0), (other["tenant_id"], 1.0)):
            cache = data_hub.HubTickCache(data_hub.org_namespace(tenant))
            await cache.put(Tick(broker="upstox", exchange="NSE", symbol="NIFTY", ltp=ltp, exchange_ts=now, received_at=now))
            await cache.flush()
    run(tick())
    data_hub._snapshot_memo.clear()
    mine = _events(client.get(f"/api/live/stream?sid={SID}", headers=headers).text)
    ticks = [d for e, d in mine if e == "ticks"]
    assert ticks and ticks[0]["items"][0]["ltp"] == 24000.0                 # his own connection's price
    data_hub._snapshot_memo.clear()
    theirs = _events(client.get(f"/api/live/stream?sid={SID}", headers=other_headers).text)   # the same tab id, B's token
    assert [e for e, _ in theirs if e == "ticks"] == []                     # nothing of A's, and B registered nothing
    assert "24000" not in json.dumps(theirs)


def test_push_streams_per_user_are_bounded_and_the_slot_comes_back(monkeypatch):
    monkeypatch.setattr(live_routes, "session_factory", _session_factory)
    monkeypatch.setattr(config, "LIVE_PUSH_MAX_STREAMS_PER_USER", 1)
    push = {"stale_after_s": 30, "beat_s": 10, "bar_settle_s": 5, "max_symbols": 50}

    async def never():
        return False

    async def go():
        first = live_routes.events(1, never, push=push, push_user=7, max_seconds=60)
        a = (await first.__anext__()).decode()
        second = live_routes.events(1, never, push=push, push_user=7, max_seconds=60)
        b = (await second.__anext__()).decode()
        third = live_routes.events(1, never, push=push, push_user=8, max_seconds=60)   # another user: his own bound
        c = (await third.__anext__()).decode()
        held = dict(live_routes._push_streams)
        await first.aclose()                                    # the tab closed: its slot is given back
        await second.aclose()
        await third.aclose()
        again = live_routes.events(1, never, push=push, push_user=7, max_seconds=60)
        d = (await again.__anext__()).decode()
        await again.aclose()
        return a, b, c, d, held
    a, b, c, d, held = run(go())
    assert '"push"' in a and '"push"' not in b and '"push"' in c and '"push"' in d
    assert held == {7: 1, 8: 1}
    assert live_routes._push_streams == {}


def test_heartbeat_is_a_beat_event_with_the_push_and_a_comment_without(monkeypatch):
    monkeypatch.setattr(live_routes, "session_factory", _session_factory)
    push = {"stale_after_s": 30, "beat_s": 0, "bar_settle_s": 5, "max_symbols": 50}

    def ends_after(n):
        calls = [0]

        async def disconnected():
            calls[0] += 1
            return calls[0] > n
        return disconnected
    with_push = run(_collect(live_routes.events(1, ends_after(2), push=push, push_user=9, heartbeat_seconds=0, poll_seconds=0)))
    without = run(_collect(live_routes.events(1, ends_after(2), heartbeat_seconds=0, poll_seconds=0)))
    assert "event: beat" in with_push and ": ping" not in with_push
    assert ": ping" in without and "event: beat" not in without
    assert live_routes._push_streams == {}                                   # a disconnect ends it cleanly


def test_a_disconnected_tab_ends_the_stream_and_frees_the_slot(monkeypatch):
    monkeypatch.setattr(live_routes, "session_factory", _session_factory)
    push = {"stale_after_s": 30, "beat_s": 10, "bar_settle_s": 5, "max_symbols": 50}

    async def gone():
        return True
    body = run(_collect(live_routes.events(1, gone, push=push, push_user=11)))
    assert body.startswith("event: ready") and "event: bye" not in body
    assert live_routes._push_streams == {}

"""SCR-FIX PR C (owner order ATP_ORDER_SCR_FIX, C2-C4):

* C2 parity: the evidence pack's bars are the bars the Charts page draws for the same symbol, timeframe and window -
  the screener's fetch step (`routes._symbol_frames` -> `evidence.build_pack`) against the candle route's shaping
  (`candles_routes._shape`, the `build_frames` path), from the same broker bars;
* C3: the Telegram image is laid out like the chart - candles and volume in the up / down colours, a volume pane, the
  bar times on the time axis and the matched bar's O / H / L / C under the title;
* C4: the evidence API says `telegram_linked` with a machine reason (not_linked / bot_not_configured / blocked_by_user /
  send_failed) and a plain sentence; a chat linked with "Connect Telegram" (the platform bot) counts, and the send after
  linking goes exactly once to that chat; never the token.
"""
from datetime import datetime, timedelta, timezone
from typing import List

import httpx
import numpy as np
import pandas as pd
import pytest

from app.core import config as app_config
from app.core.models import OHLCVBar
from app.market_data import candles_routes, intervals
from app.screener import compile_screen
from app.screener import evidence as ev
from app.screener import evidence_png as epng
from app.screener import routes as screener_routes
from app.screener.evidence_store import view
from app.screener.runtime import SymbolData
from tests.test_auth_api import _session_factory, client
from tests.test_phase_l_ai import _owner
from tests.test_u8_evidence_api import Telegram, _run, _screen_run, _serve_bars, _set, screener_on  # noqa: F401 - the fixture

PLATFORM_TOKEN = "654321:PLATFORM-test-token"
IST = timezone(timedelta(hours=5, minutes=30))


# ------------------------------------------------------------------------------------------------------------ C2 parity
def _minute_bars(sessions: int = 2) -> List[OHLCVBar]:
    out: List[OHLCVBar] = []
    rng = np.random.default_rng(7)
    price = 500.0
    for d in range(sessions):
        start = datetime(2026, 10, 19 + d, 9, 15, tzinfo=IST).astimezone(timezone.utc)
        for m in range(375):                                   # 09:15 .. 15:29 IST, one-minute bars
            o = price
            price = max(1.0, price + float(rng.normal(0, 0.6)))
            hi, lo = max(o, price) + 0.2, min(o, price) - 0.2
            out.append(OHLCVBar(timestamp=start + timedelta(minutes=m), open=o, high=hi, low=lo, close=price, volume=float(100 + m)))
    return out


def _daily_bars(days: int = 90) -> List[OHLCVBar]:
    idx = pd.date_range("2026-05-04", periods=days, freq="B", tz=IST)
    closes = 200 + np.cumsum(np.sin(np.arange(days) / 3))
    opens = np.concatenate([[closes[0] - 0.4], closes[:-1]])     # each bar opens at the last close: rising and falling bars
    return [OHLCVBar(timestamp=t.to_pydatetime(), open=float(o), high=max(o, c) + 1, low=min(o, c) - 1, close=float(c), volume=1000.0 + i)
            for i, (t, o, c) in enumerate(zip(idx, opens, closes))]


@pytest.mark.parametrize("screen_tf, chart_tf, bars", [("1d", "day", _daily_bars()), ("3m", "3min", _minute_bars())])
def test_c2_evidence_bars_equal_the_charts_bars(screen_tf, chart_tf, bars):
    now = bars[-1].timestamp + timedelta(days=1)               # every bar closed: the chart has no forming bar either
    interval = screener_routes._FETCH[screen_tf]
    fetched = bars if interval == "day" else intervals.reshape(bars, "1min", interval)  # SCR-FIX A1: the fetch step's bars
    _df, frames, problem = screener_routes._symbol_frames(fetched, interval, screen_tf, False, now)
    assert problem == ""
    ast, validated = compile_screen("close > SMA(close, 20)", base_tf=screen_tf)
    pack = view(ev.build_pack(ast, validated, SymbolData("PAR", frames, exchange="NSE"), base_tf=screen_tf, window=60))
    chart = candles_routes._shape(bars, chart_tf)               # what POST /api/market-data/candles answers (build_frames path)
    n = len(pack["bars"]["t"])
    assert n == 60 and len(chart) >= n
    tail = chart[-n:]
    assert [pd.Timestamp(t) for t in pack["bars"]["t"]] == [pd.Timestamp(b.timestamp) for b in tail]
    for key, attr in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume")):
        assert pack["bars"][key] == pytest.approx([getattr(b, attr) for b in tail]), key
    assert pack["matched_index"] == n - 1                      # the matched bar is the chart's newest closed bar


# ---------------------------------------------------------------------------------------------------------- C3 image
def _mixed_pack():
    bars = _daily_bars()
    _df, frames, _p = screener_routes._symbol_frames(bars, "day", "1d", False, bars[-1].timestamp + timedelta(days=1))
    ast, validated = compile_screen("close > SMA(close, 20)", base_tf="1d")
    return view(ev.build_pack(ast, validated, SymbolData("MIX", frames, exchange="NSE"), base_tf="1d", window=60))


def _rgb(px: bytes) -> set:
    return {px[i:i + 3] for i in range(0, len(px), 3)}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_c3_the_image_has_up_down_candles_volume_and_a_time_axis(theme):
    pack = _mixed_pack()
    th = epng.THEMES[theme]
    px = epng.pixels(pack, theme=theme)
    assert px == epng.pixels(pack, theme=theme)                                         # deterministic
    colours = _rgb(px)
    assert bytes(th.up) in colours and bytes(th.down) in colours                         # rising and falling bars both drawn
    w, h = epng.WIDTH, epng.HEIGHT

    def row_band(y0: int, y1: int, x0: int = 10, x1: int = epng.WIDTH - 112) -> set:
        return {px[(y * w + x) * 3:(y * w + x) * 3 + 3] for y in range(y0, y1) for x in range(x0, x1)}
    floor = h - 18 - 6 - 16
    vol_band = row_band(floor - epng.VOLUME_PANE_HEIGHT + 12, floor - 2)
    assert len(vol_band - {bytes(th.bg), bytes(th.grid), bytes(th.axis), bytes(th.marker)}) >= 2   # faded up / down volume bars
    assert bytes(th.muted) in row_band(floor + 4, floor + 16)                            # the time labels under the panes
    assert bytes(th.muted) in row_band(18, 32)                                           # the matched bar's O / H / L / C line
    labels = epng._time_labels(pack["bars"]["t"], "1d")
    assert labels[-1] == pd.Timestamp(pack["bars"]["t"][-1]).tz_convert("Asia/Kolkata").strftime("%d %b %Y")
    assert epng._time_labels(["2026-10-19T05:45:00+00:00"], "15m") == ["19 Oct 11:15"]       # intraday: IST with the time
    j = pack["matched_index"]
    assert epng._ohlc_text(*(pack["bars"][k][j] for k in "ohlcv")).startswith(f"O {pack['bars']['o'][j]:,.2f}")
    assert epng.render(pack, theme=theme)[:8] == b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------------------------------------- C4 Telegram
async def _platform_link(tenant_id: int, user_id: int, chat: str) -> None:
    """What "Connect Telegram" does: a one-time code, then the user presses Start in their private chat with the bot."""
    from app.alerts import platform_bot
    async with _session_factory() as session:
        code, _ = await platform_bot.new_code(session, tenant_id, user_id)
        record, why = await platform_bot.consume(session, code, chat, chat)
        assert record is not None and why == "linked"
        await session.commit()


def _telegram_answer(status: int, description: str):
    def make():
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"ok": False, "description": description})))
    return make


def test_c4_linked_flag_reason_codes_and_one_send_after_connect(screener_on, monkeypatch):  # noqa: F811 - the fixture
    _set(evidence_charts=True)
    headers, me = _owner("scr-c4-link@example.com")
    _serve_bars(monkeypatch)
    eid = _screen_run(headers, symbols=["UP"])["results"][0]["evidence_id"]
    monkeypatch.setattr(app_config, "ATP_TELEGRAM_BOT_TOKEN", "")
    pack = client.get(f"/api/screener/evidence/{eid}", headers=headers).json()
    assert (pack["telegram_linked"], pack["telegram_reason"]) == (False, "bot_not_configured")    # the page hides the button
    assert pack["telegram_message"] == "Telegram is not set up on this server yet."

    monkeypatch.setattr(app_config, "ATP_TELEGRAM_BOT_TOKEN", PLATFORM_TOKEN)
    pack = client.get(f"/api/screener/evidence/{eid}", headers=headers).json()
    assert (pack["telegram_linked"], pack["telegram_reason"]) == (False, "not_linked")            # the page shows Connect
    refused = client.post(f"/api/screener/evidence/{eid}/telegram", headers=headers)
    assert refused.status_code == 409 and refused.json()["detail"] == {"reason": "not_linked", "message": "Connect your Telegram to get this chart there."}

    _run(_platform_link(me["tenant_id"], me["id"], "5151"))
    pack = client.get(f"/api/screener/evidence/{eid}", headers=headers).json()
    assert (pack["telegram_linked"], pack["telegram_reason"], pack["telegram_message"]) == (True, None, None)
    tg = Telegram()
    from app.ai import vision_audit
    monkeypatch.setattr(vision_audit, "telegram_client", tg.client)
    sent = client.post(f"/api/screener/evidence/{eid}/telegram", headers=headers)                 # the page's one send after linking
    assert sent.status_code == 200 and sent.json()["sent"] is True and PLATFORM_TOKEN not in sent.text
    assert [(s["method"], s["chat_id"], s["png"]) for s in tg.sent] == [("sendPhoto", "5151", True)]
    assert PLATFORM_TOKEN not in str(pack)


@pytest.mark.parametrize("status, description, reason", [
    (403, "Forbidden: bot was blocked by the user", "blocked_by_user"),
    (400, "Bad Request: chat not found", "blocked_by_user"),
    (500, "Internal Server Error", "send_failed"),
])
def test_c4_telegram_refusals_become_reason_codes(screener_on, monkeypatch, status, description, reason):  # noqa: F811 - the fixture
    _set(evidence_charts=True)
    headers, me = _owner(f"scr-c4-{status}-{reason}@example.com")
    _serve_bars(monkeypatch)
    eid = _screen_run(headers, symbols=["UP"])["results"][0]["evidence_id"]
    monkeypatch.setattr(app_config, "ATP_TELEGRAM_BOT_TOKEN", PLATFORM_TOKEN)
    _run(_platform_link(me["tenant_id"], me["id"], "6161"))
    from app.ai import vision_audit
    monkeypatch.setattr(vision_audit, "telegram_client", _telegram_answer(status, description))
    out = client.post(f"/api/screener/evidence/{eid}/telegram", headers=headers)
    assert out.status_code == 409 and out.json()["detail"]["reason"] == reason and PLATFORM_TOKEN not in out.text
    assert out.json()["detail"]["message"] and description not in out.text                       # a plain sentence, not Telegram's

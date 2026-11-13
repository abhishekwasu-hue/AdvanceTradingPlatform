"""Owner order 13 Nov: the opposite call / put OI move alert (app/option_chain/oi_opposite.py, raised by oi_alerts).

Covered: opposite moves above the levels alert; both up / both down / below Trade's % / not sudden against the recent
windows do not; the 5 / 10 / 15 windows (closed windows only); no look-ahead (a later slot never changes an earlier
result); a missing window is not compared; the text is neutral; the service sends once through the existing notify path
(a spy), the cooldown holds the next one, nothing without the symbol's own bell, the History rows carry the mark; Trade
parity of the opposite rule (ATP_TRADE_REPO, skipped otherwise).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete

from app.db.models import (
    OIAlertLogRecord,
    OIBannerSettingRecord,
    OIBannerStateRecord,
    OIDayBaselineRecord,
    OISnapshotRecord,
    StrikeOISnapshotRecord,
)
from app.option_chain import oi_alerts, oi_opposite, oi_regime
from app.option_chain.oi_opposite import Point
from app.option_chain.oi_regime import OIAlertSettings
from tests.test_auth_api import _session_factory, client

T0 = datetime(2026, 3, 10, 4, 0, tzinfo=UTC)          # IST 09:30, a 5 / 10 / 15-minute boundary


def _run(coro):
    return asyncio.run(coro)


def _pts(pairs, step=5):
    return [Point(T0 + timedelta(minutes=step * i), float(c), float(p)) for i, (c, p) in enumerate(pairs)]


QUIET = [(1000, 1000), (1001, 1001), (1002, 1000), (1003, 1001)]       # small moves: the recent average


def S(**kw):
    return OIAlertSettings(types=["OPPOSITE_OI"], **kw)


# ---------------------------------------------------------------- the rule (pure)

def test_opposite_moves_above_the_levels_alert():
    pts = _pts(QUIET + [(1100, 950)])                       # call +97 (+9.4 %), put -51 (-5.1 %)
    found = oi_opposite.moves(pts, S(), 5)
    assert len(found) == 1
    m = found[0]
    assert m.slot_start == pts[-1].slot_start and m.call_change == 97 and m.put_change == -51 and m.window == 5
    assert m.rotation == oi_regime.Direction.BEARISH and m.state() == "CE +97 PE -51 5m"
    rev = oi_opposite.moves(_pts(QUIET + [(950, 1100)]), S(), 5)
    assert len(rev) == 1 and rev[0].rotation == oi_regime.Direction.BULLISH


@pytest.mark.parametrize("last", [(1100, 1100), (900, 900)], ids=["both-up", "both-down"])
def test_same_direction_never_alerts(last):
    assert oi_opposite.moves(_pts(QUIET + [last]), S(), 5) == []


def test_below_trades_percent_or_not_sudden_does_not_alert():
    assert oi_opposite.moves(_pts(QUIET + [(1015, 990)]), S(), 5) == []                       # 1.2 % / 1.1 % < 2 %
    noisy = [(1000, 1000), (1100, 900), (1000, 1000), (1100, 900), (1000, 1000)]                # |change| ~100 every window
    assert [m.slot_start for m in oi_opposite.moves(_pts(noisy + [(1100, 900)]), S(opposite_relative_k=2.0), 5)] == []
    assert len(oi_opposite.moves(_pts(noisy + [(1100, 900)]), S(opposite_relative_k=0.5), 5)) >= 1   # his k, lower
    assert oi_opposite.moves(_pts([(1000, 1000), (1100, 900)]), S(), 5) == []                   # nothing earlier to compare


def test_recent_average_reads_only_the_last_m_windows():
    big_then_quiet = [(1000, 1000), (2000, 500), (2001, 501), (2002, 500), (2003, 501)]
    last = (2200, 450)                                       # call +197, put -51
    assert oi_opposite.moves(_pts(big_then_quiet + [last]), S(opposite_relative_windows=6), 5) == []   # the big one counts
    assert len(oi_opposite.moves(_pts(big_then_quiet + [last]), S(opposite_relative_windows=3), 5)) == 1


@pytest.mark.parametrize("window", [5, 10, 15])
def test_windows_compare_closed_window_ends(window):
    per = window // 5
    seq = []
    for i in range(5):                                       # five quiet windows
        seq += [(1000 + i, 1000 + (i % 2))] * per
    seq += [(1150, 900)] * per                               # the sixth window moves opposite from its first slot
    pts = _pts(seq)
    found = oi_opposite.moves(pts, S(opposite_window_minutes=window), 5)
    assert [m.slot_start for m in found] == [pts[-1].slot_start] and found[0].window == window
    if per > 1:                                              # its last slot not stored yet: the window is open, no alert
        assert oi_opposite.moves(pts[:-1], S(opposite_window_minutes=window), 5) == []


def test_no_look_ahead_and_missing_window_is_skipped():
    pts = _pts(QUIET + [(1100, 950), (1200, 900), (1300, 800)])
    full = oi_opposite.moves(pts, S(), 5)
    for cut in range(1, len(pts) + 1):
        upto = pts[cut - 1].slot_start
        assert oi_opposite.moves(pts, S(), 5, until=upto) == [m for m in full if m.slot_start <= upto]
        assert oi_opposite.moves(pts[:cut], S(), 5) == [m for m in full if m.slot_start <= upto]
    gap = QUIET + [(1100, 950)]
    gapped = [p if i < len(gap) - 1 else Point(p.slot_start + timedelta(minutes=10), p.call_oi, p.put_oi) for i, p in enumerate(_pts(gap))]
    assert oi_opposite.moves(gapped, S(), 5) == []


def test_text_is_neutral_and_carries_the_numbers():
    m = oi_opposite.moves(_pts([(10e5, 10e5), (10.01e5, 10e5), (11.24e5, 9.19e5)]), S(), 5)[0]
    r = oi_opposite.render("NIFTY", m, S(), 6, "2026-03-17", m.slot_start, m.slot_start, oi_alerts.SCHEMA)
    text = (r["title"] + " " + r["message"]).lower()
    for word in ("buy", "sell", "target", "stop", "bullish", "bearish", "entry"):
        assert word not in text
    assert r["message"].startswith("NIFTY 17 Mar: Call OI +1.23 L (+12.3%) and Put OI −0.81 L (−8.1%) in 5 min (ATM ± 6)")
    assert "Data as of 10 Mar 09:40 IST" in r["message"] and r["payload"]["alert_type"] == "OPPOSITE_OI"
    assert r["payload"]["detail"]["rotation"] == "BEARISH"            # internal value: payload only


def test_settings_are_off_until_he_turns_them_on():
    s = OIAlertSettings()
    assert "OPPOSITE_OI" not in s.types
    assert (s.opposite_window_minutes, s.opposite_min_change_pct, s.opposite_relative_k, s.opposite_relative_windows) == (5, 2.0, 2.0, 6)
    with pytest.raises(ValueError):
        OIAlertSettings(opposite_window_minutes=7)
    assert oi_opposite.bell_on(None) is False
    assert oi_opposite.bell_on(OIBannerSettingRecord(overrides=json.dumps({"alerts": {"types": ["OPPOSITE_OI"]}}))) is True
    assert oi_opposite.bell_on(OIBannerSettingRecord(overrides=json.dumps({"alerts": {"types": ["PCR_BAND"]}}))) is False


# ---------------------------------------------------------------- Trade parity (read-only checkout)

TRADE = os.environ.get("ATP_TRADE_REPO", "")


@pytest.mark.skipif(not TRADE or not (Path(TRADE) / "oi_analysis.py").is_file(), reason="Trade checkout not mounted (ATP_TRADE_REPO)")
def test_parity_with_trades_rotation_rule():
    sys.path.insert(0, TRADE)
    try:
        oa = importlib.import_module("oi_analysis")
    finally:
        sys.path.remove(TRADE)
    import random
    rnd = random.Random(13)
    for _ in range(500):
        c0, p0 = rnd.uniform(1e5, 1e7), rnd.uniform(1e5, 1e7)
        c1, p1 = c0 * (1 + rnd.uniform(-0.06, 0.06)), p0 * (1 + rnd.uniform(-0.06, 0.06))
        trade = oa.is_genuine_rotation(c1, c0, p1, p0, 2.0)
        ours = oi_regime.is_genuine_rotation(c1, c0, p1, p0, 2.0)
        assert trade == (ours.value if ours else None)
        # with k small enough to always pass, the alert fires exactly when Trade's rule does
        found = oi_opposite.moves([Point(T0, c0, p0), Point(T0 + timedelta(minutes=5), c0, p0), Point(T0 + timedelta(minutes=10), c1, p1)],
                                  S(opposite_relative_k=1e-9), 5)
        assert bool(found) == (trade is not None) or (c0 == c1 or p0 == p1)


# ---------------------------------------------------------------- the service (existing notify path, a spy)

@pytest.fixture()
def _clean_oi():
    async def go():
        async with _session_factory() as session:
            for model in (OIAlertLogRecord, OIBannerStateRecord, StrikeOISnapshotRecord, OISnapshotRecord, OIDayBaselineRecord, OIBannerSettingRecord):
                await session.execute(delete(model))
            await session.commit()
    _run(go())
    yield


def _evaluate_sequence(tenant_id, seq, monkeypatch):
    from tests.test_o2_oi_snapshots import UND, _collect
    from tests.test_o2_oi_snapshots import _chain as oi_chain
    sent: list[tuple] = []
    real = oi_alerts.notify

    async def spy(session, tenant, kind, title, message, **kw):
        sent.append((tenant, kind.value, title, message, json.loads(kw.get("metadata_json") or "{}")))
        return await real(session, tenant, kind, title, message, **kw)
    monkeypatch.setattr(oi_alerts, "notify", spy)
    out = []
    for i, (c, p) in enumerate(seq):
        _run(_collect(oi_chain(call=c, put=p), T0 + timedelta(minutes=5 * i), tenant_id=tenant_id))

        async def ev(when=T0 + timedelta(minutes=5 * i, seconds=30)):
            async with _session_factory() as session:
                return await oi_alerts.evaluate_tenant(session, tenant_id, UND, when)
        out.append(_run(ev()))
    return out, sent


SEQ = [(1000.0, 2000.0), (1001.0, 2001.0), (1000.0, 2002.0), (1001.0, 2001.0), (1100.0, 1900.0), (1000.0, 2000.0), (1100.0, 1800.0)]


def test_service_sends_once_through_notify_and_respects_the_cooldown(_clean_oi, monkeypatch):
    from tests.test_o2_oi_snapshots import UND
    from tests.test_phase_l_ai import _owner
    headers, me = _owner(f"opp-{uuid.uuid4().hex[:6]}@example.com")
    alerts = {"types": ["OPPOSITE_OI"], "cooldown_minutes": 60}
    assert client.put(f"/api/option-chain/{UND}/settings", json={"enabled": True, "overrides": {"alerts": alerts}}, headers=headers).status_code == 200
    out, sent = _evaluate_sequence(me["tenant_id"], SEQ, monkeypatch)
    # slot 4 moves opposite (sent); slots 5 and 6 move opposite again inside the cooldown (held, logged)
    assert out[:4] == [[], [], [], []] and out[4] == ["SENT"] and out[5] == ["COOLDOWN"] and out[6] == ["COOLDOWN"]
    assert len(sent) == 1 and sent[0][1] == "OI_BANNER" and sent[0][4]["alert_type"] == "OPPOSITE_OI"
    assert "opposite OI activity" in sent[0][3] and "in 5 min (ATM ± 6)" in sent[0][3]
    log = client.get(f"/api/option-chain/{UND}/alerts", params={"date": "2026-03-10"}, headers=headers).json()
    assert [a["status"] for a in log["alerts"] if a["alert_type"] == "OPPOSITE_OI"] == ["COOLDOWN", "COOLDOWN", "SENT"]
    rows = client.get(f"/api/option-chain/{UND}/history", params={"date": "2026-03-10", "interval": 5}, headers=headers).json()["rows"]
    marked = [r for r in rows if "opposite" in r]
    assert marked and marked[-1]["opposite"]["window"] == 5 and marked[-1]["opposite"]["call_change"] > 0


def test_service_sends_nothing_without_the_symbols_bell(_clean_oi, monkeypatch):
    from tests.test_o2_oi_snapshots import UND
    from tests.test_phase_l_ai import _owner
    headers, me = _owner(f"opp-off-{uuid.uuid4().hex[:6]}@example.com")
    # the organisation-wide default ("*") with OPPOSITE_OI never turns it on for a symbol (owner answer 13 Nov)
    assert client.put("/api/option-chain/*/settings", json={"overrides": {"alerts": {"types": ["OPPOSITE_OI"]}}}, headers=headers).status_code == 200
    assert client.put(f"/api/option-chain/{UND}/settings", json={"enabled": True, "overrides": {}}, headers=headers).status_code == 200
    out, sent = _evaluate_sequence(me["tenant_id"], SEQ, monkeypatch)
    assert all(o == [] for o in out) and sent == []
    rows = client.get(f"/api/option-chain/{UND}/history", params={"date": "2026-03-10"}, headers=headers).json()["rows"]
    assert all("opposite" not in r for r in rows)

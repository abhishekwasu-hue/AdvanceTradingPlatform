"""M96 N1: OI over a from-to range, the replay frames and the multi-expiry summary from the 1-minute chain store, and
the per-strike OI / PCR threshold alerts.

Covered (T1): the range maths against a two-slot fixture store (per-strike diff, hand-computed); the replay frames (one
per stored chain, oldest first, each from its own chain only); the multi-expiry aggregation (totals, change since the
day's first reading, PCR, max pain, ATM IV); the read API (own rows only, flag-gated, "no data" states, no look-ahead:
a chain read after the asked time is never used); the alert detection (off by default, fires once on the crossing,
not again above it, cooldown per rule, PCR both ways) and the service end to end through the existing notify path.
"""
import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.brokers.models import OptionChain, OptionChainRow
from app.db.models import (
    NotificationRecord,
    OIAlertLogRecord,
    OIBannerSettingRecord,
    OIBannerStateRecord,
    OIDayBaselineRecord,
    OISnapshotRecord,
    StrikeOISnapshotRecord,
)
from app.market_data.calendar import IST
from app.market_lake import chain_range, chain_recorder, chain_store
from app.market_lake.chain_store import StoredChain
from app.option_chain import oi_alerts
from app.option_chain.oi_alerts import ThresholdView
from app.option_chain.oi_regime import OIAlertSettings, StrikeOIRule
from tests.test_auth_api import _session_factory, client
from tests.test_tm94_chain_history import _flag, _uniq, _user

DAY = date(2026, 3, 10)
EXP1, EXP2 = date(2026, 3, 12), date(2026, 3, 26)
# IST 10:00:20 and 10:05:20 reads (two slots)
T1 = datetime(2026, 3, 10, 10, 0, 20, tzinfo=IST).astimezone(timezone.utc)
T2 = T1 + timedelta(minutes=5)


def _run(coro):
    return asyncio.run(coro)


def _rows(spec):
    """spec: {strike: (call_oi, put_oi, call_ltp, put_ltp, call_iv, put_iv)}"""
    return [OptionChainRow(strike=k, call_oi=v[0], put_oi=v[1], call_ltp=v[2], put_ltp=v[3], call_iv=v[4], put_iv=v[5])
            for k, v in sorted(spec.items())]


A = {100.0: (1000.0, 3000.0, 12.0, 1.0, 20.0, 22.0), 110.0: (2000.0, 2000.0, 5.0, 5.0, 18.0, 18.0), 120.0: (4000.0, 500.0, 1.0, 11.0, 19.0, 21.0)}
B = {100.0: (1200.0, 3600.0, 11.0, 1.5, 20.0, 22.0), 110.0: (1500.0, 2600.0, 4.0, 6.0, 18.0, 20.0), 120.0: (4300.0, 500.0, 0.5, 12.0, 19.0, 21.0),
     130.0: (900.0, 100.0, 0.2, 20.0, 25.0, 25.0)}


def _stored(spec, at, spot=111.0, expiry=EXP1):
    return StoredChain("X", expiry, at.replace(second=0), at, spot, _rows(spec))


# ---------------------------------------------------------------- pure maths

def test_range_diff_per_strike_is_hand_computed():
    d = chain_range.oi_diff(_stored(A, T1), _stored(B, T2))
    by = {r["strike"]: r for r in d["rows"]}
    assert [r["strike"] for r in d["rows"]] == [100.0, 110.0, 120.0, 130.0]
    assert (by[100.0]["ce_change"], by[100.0]["pe_change"], by[100.0]["diff"]) == (200.0, 600.0, 400.0)
    assert (by[110.0]["ce_change"], by[110.0]["pe_change"], by[110.0]["diff"]) == (-500.0, 600.0, 1100.0)
    assert (by[120.0]["ce_change"], by[120.0]["pe_change"]) == (300.0, 0.0)
    assert by[130.0]["ce_from"] is None and by[130.0]["ce_change"] is None and by[130.0]["diff"] is None   # not in the start chain
    assert d["totals"] == {"ce_change": 0.0, "pe_change": 1200.0, "diff": 1200.0}
    assert d["atm"] == 110.0 and d["from"]["captured_at"].startswith("2026-03-10T10:00:20") and d["to"]["captured_at"].startswith("2026-03-10T10:05:20")


def test_replay_frames_step_through_each_stored_chain_in_order():
    out = chain_range.replay_frames([_stored(B, T2), _stored(A, T1)])          # given out of order
    assert out["strikes"] == [100.0, 110.0, 120.0, 130.0]
    assert [f["captured_at"][11:19] for f in out["frames"]] == ["10:00:20", "10:05:20"]
    assert out["frames"][0]["ce"] == [1000.0, 2000.0, 4000.0, None] and out["frames"][1]["pe"] == [3600.0, 2600.0, 500.0, 100.0]
    narrow = chain_range.replay_frames([_stored(A, T1, spot=100.0)], around=1)
    assert narrow["strikes"] == [100.0, 110.0, 120.0]                                # 3 strikes: no narrowing needed
    wide = {float(k): (1.0, 1.0, 1.0, 1.0, 10.0, 10.0) for k in range(100, 200, 10)}
    w = chain_range.replay_frames([_stored(wide, T1, spot=150.0), _stored(wide, T2, spot=171.0)], around=1)
    assert w["strikes"] == [140.0, 150.0, 160.0, 170.0, 180.0]                       # each frame's own ATM +-1, joined


def test_expiry_summary_aggregates_one_expiry():
    s = chain_range.expiry_summary(_stored(B, T2), _stored(A, T1))
    assert s["total_call_oi"] == 7900.0 and s["total_put_oi"] == 6800.0
    assert s["call_oi_change"] == 0.0 and s["put_oi_change"] == 1200.0
    assert s["pcr_oi"] == pytest.approx(6800.0 / 7900.0)
    assert s["atm_strike"] == 110.0 and s["expiry"] == EXP1.isoformat() and s["since"].startswith("2026-03-10T10:00:20")
    assert s["max_pain"] in (100.0, 110.0, 120.0, 130.0) and s["atm_iv"] is not None
    alone = chain_range.expiry_summary(_stored(B, T2), None)
    assert alone["call_oi_change"] is None and alone["since"] is None


# ---------------------------------------------------------------- the read API

def _chain(spec, spot=111.0):
    return OptionChain(underlying="X", expiry="", underlying_ltp=spot, rows=_rows(spec))


def _write(tenant_id, user_id, name, spec, at, expiry=EXP1, spot=111.0):
    async def go():
        async with _session_factory() as session:
            r = await chain_store.write(session, tenant_id, name, expiry, _chain(spec, spot), at, slot_minutes=1, source=f"broker:upstox:u{user_id}")
            await session.commit()
            return r.status
    return _run(go())


def test_range_replay_and_expiries_read_only_his_own_rows():
    headers, me = _user(f"m96-own-{uuid.uuid4().hex[:6]}@example.com")
    other_headers, other = _user(f"m96-other-{uuid.uuid4().hex[:6]}@example.com")
    name = _uniq("M96")
    assert _write(me.tenant_id, me.id, name, A, T1) == "OK"
    assert _write(me.tenant_id, me.id, name, B, T2) == "OK"
    assert _write(me.tenant_id, me.id, name, B, T2 + timedelta(minutes=10)) == "OK"         # read after the asked "To"
    assert _write(me.tenant_id, me.id, name, A, T1, expiry=EXP2) == "OK"
    q = {"underlying": name, "expiry": EXP1.isoformat(), "start": "2026-03-10T10:00:30", "end": "2026-03-10T10:06:00"}
    _flag(chain_recorder.FLAG, False)
    assert client.get("/api/chain-history/oi-range", params=q, headers=headers).status_code == 404
    _flag(chain_recorder.FLAG, True)
    try:
        r = client.get("/api/chain-history/oi-range", params=q, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["missing"] is None and body["to"]["captured_at"].startswith("2026-03-10T10:05:20")   # not the 10:15 read
        assert {x["strike"]: x["diff"] for x in body["rows"]}[110.0] == 1100.0
        early = client.get("/api/chain-history/oi-range", params={**q, "start": "2026-03-10T09:30", "end": "2026-03-10T09:45"}, headers=headers).json()
        assert early["missing"] == "both" and early["rows"] == []
        same = client.get("/api/chain-history/oi-range", params={**q, "start": "2026-03-10T10:01", "end": "2026-03-10T10:02"}, headers=headers).json()
        assert same["missing"] == "same"
        assert client.get("/api/chain-history/oi-range", params={**q, "end": "2026-03-10T09:00"}, headers=headers).status_code == 422
        theirs = client.get("/api/chain-history/oi-range", params=q, headers=other_headers).json()
        assert theirs["missing"] == "both"

        rep = client.get("/api/chain-history/oi-replay", params={"underlying": name, "expiry": EXP1.isoformat(), "day": DAY.isoformat()},
                         headers=headers).json()
        assert len(rep["frames"]) == 3 and rep["strikes"] == [100.0, 110.0, 120.0, 130.0]
        assert client.get("/api/chain-history/oi-replay", params={"underlying": name, "expiry": EXP1.isoformat(), "day": DAY.isoformat()},
                          headers=other_headers).json()["frames"] == []

        ex = client.get("/api/chain-history/expiries", params={"underlying": name, "at": "2026-03-10T10:06:00"}, headers=headers).json()
        assert [e["expiry"] for e in ex["expiries"]] == [EXP1.isoformat(), EXP2.isoformat()]
        near = ex["expiries"][0]
        assert near["captured_at"].startswith("2026-03-10T10:05:20") and near["put_oi_change"] == 1200.0
        assert ex["expiries"][1]["call_oi_change"] is None                          # one reading only: no change yet
        assert client.get("/api/chain-history/expiries", params={"underlying": name, "at": "2026-03-10T10:06:00"},
                          headers=other_headers).json()["expiries"] == []
    finally:
        _flag(chain_recorder.FLAG, False)


# ---------------------------------------------------------------- the threshold alerts (pure)

def test_threshold_alerts_are_off_by_default_and_fire_once_on_the_crossing():
    prev = ThresholdView(1.0, {("CE", 24500.0): 300.0})
    cur = ThresholdView(1.3, {("CE", 24500.0): 600.0})
    assert oi_alerts.detect_thresholds(prev, cur, OIAlertSettings()) == []                       # nothing set
    assert oi_alerts.detect_thresholds(prev, cur, OIAlertSettings(enabled=True)) == []           # types not ticked, no levels
    rule = StrikeOIRule(strike=24500, side="CE", threshold=500)
    s = OIAlertSettings(enabled=True, types=["STRIKE_OI_CHANGE", "PCR_CROSS"], strike_rules=[rule], pcr_level=1.2)
    ev = oi_alerts.detect_thresholds(prev, cur, s)
    assert [e.alert_type for e in ev] == ["STRIKE_OI_CHANGE", "PCR_CROSS"]
    assert ev[0].new_state == "CE 24500 reached 500" and ev[0].detail["change"] == 600.0 and ev[1].new_state == "above 1.2"
    stays = ThresholdView(1.4, {("CE", 24500.0): 900.0})
    assert oi_alerts.detect_thresholds(cur, stays, s) == []                                       # still above: no repeat
    back = ThresholdView(1.1, {("CE", 24500.0): 100.0})
    assert [e.new_state for e in oi_alerts.detect_thresholds(stays, back, s)] == ["below 1.2"]  # PCR crosses down too
    assert oi_alerts.detect_thresholds(None, cur, s) == []                                        # first slot: no comparison
    assert oi_alerts.detect_thresholds(prev, ThresholdView(1.3, {}), s)[0].alert_type == "PCR_CROSS"   # strike not read: nothing
    with pytest.raises(ValueError):
        StrikeOIRule(strike=24500, side="CE", threshold=0)


def test_threshold_alert_text_is_neutral():
    s = OIAlertSettings(enabled=True, types=["STRIKE_OI_CHANGE", "PCR_CROSS"], strike_rules=[StrikeOIRule(strike=24500, side="PE", threshold=2e5)],
                        pcr_level=1.0)
    ev = oi_alerts.detect_thresholds(ThresholdView(0.9, {("PE", 24500.0): 1e5}), ThresholdView(1.1, {("PE", 24500.0): 2.5e5}), s)
    for e in ev:
        r = oi_alerts.render_threshold("NIFTY", e, T1, T1)
        text = r["title"] + " " + r["message"]
        assert "buy" not in text.lower() and "sell" not in text.lower() and "target" not in text.lower()
        assert "Data as of 10 Mar 10:00 IST" in r["message"] and r["payload"]["alert_type"] == e.alert_type
    assert "2.50 L" in oi_alerts.render_threshold("NIFTY", ev[0], T1, T1)["message"]


def test_strike_changes_use_the_day_baseline_then_the_first_slot():
    rows = _rows({24500.0: (1500.0, 900.0, 1, 1, 1, 1), 24550.0: (700.0, None, 1, 1, 1, 1)})
    first = _rows({24550.0: (500.0, 100.0, 1, 1, 1, 1)})
    out = oi_alerts.strike_changes(rows, {24500.0: (1000.0, 1000.0)}, first)
    assert out == {("CE", 24500.0): 500.0, ("PE", 24500.0): -100.0, ("CE", 24550.0): 200.0}


# ---------------------------------------------------------------- the service (existing notify path)

@pytest.fixture()
def _clean_oi():
    async def go():
        async with _session_factory() as session:
            for model in (OIAlertLogRecord, OIBannerStateRecord, StrikeOISnapshotRecord, OISnapshotRecord, OIDayBaselineRecord, OIBannerSettingRecord):
                await session.execute(delete(model))
            await session.commit()
    _run(go())
    yield


def test_strike_alert_fires_once_and_respects_the_cooldown(_clean_oi):
    from tests.test_o2_oi_snapshots import T0, UND, _collect
    from tests.test_o2_oi_snapshots import _chain as oi_chain
    from tests.test_phase_l_ai import _owner
    headers, me = _owner(f"m96-alert-{uuid.uuid4().hex[:6]}@example.com")
    alerts = {"enabled": True, "cooldown_minutes": 60, "types": ["STRIKE_OI_CHANGE"],
              "strike_rules": [{"strike": 24500, "side": "CE", "threshold": 400}]}
    assert client.put(f"/api/option-chain/{UND}/settings", json={"enabled": True, "overrides": {"alerts": alerts}}, headers=headers).status_code == 200
    calls = [1000.0, 1500.0, 1600.0, 1000.0, 1500.0]

    def bump(i):
        return lambda k: ((calls[i] if k == 24500.0 else 1000.0), 2000.0)
    out = []
    for i in range(len(calls)):
        _run(_collect(oi_chain(bump=bump(i)), T0 + timedelta(minutes=5 * i), tenant_id=me["tenant_id"]))

        async def ev(when=T0 + timedelta(minutes=5 * i, seconds=30)):
            async with _session_factory() as session:
                return await oi_alerts.evaluate_tenant(session, me["tenant_id"], UND, when)
        out.append(_run(ev()))
    assert out[0] == [] and out[1] == ["SENT"] and out[2] == [] and out[3] == [] and out[4] == ["COOLDOWN"]

    async def notes():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.tenant_id == me["tenant_id"],
                                                                               NotificationRecord.event_type == "OI_BANNER")))
    sent = _run(notes())
    assert len(sent) == 1 and "call OI change at 24500" in sent[0].title
    assert json.loads(sent[0].metadata_json)["detail"]["change"] == 500.0
    log = client.get(f"/api/option-chain/{UND}/alerts", params={"date": "2026-03-10"}, headers=headers).json()
    assert {a["status"] for a in log["alerts"] if a["alert_type"] == "STRIKE_OI_CHANGE"} == {"SENT", "COOLDOWN"}

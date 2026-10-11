"""OI Banner O4a: alerts on banner state changes - pure change detection, quiet hours, the message template and webhook
payload, and the service: one notification per change, dedupe per slot, cooldown, snooze / mute, collector stale."""
import asyncio
import json
import re
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.enums import NotificationSeverity
from app.db.models import NotificationRecord, OIAlertLogRecord, OIBannerSettingRecord, OIBannerStateRecord, OIDayBaselineRecord, OISnapshotRecord, StrikeOISnapshotRecord
from app.option_chain import oi_alerts, oi_regime
from app.option_chain.oi_alerts import StateView
from app.option_chain.oi_regime import OIAlertSettings
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_o2_oi_snapshots import T0, UND, _chain, _collect
from tests.test_phase_l_ai import _owner


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean():
    async def go():
        async with _session_factory() as session:
            for model in (OIAlertLogRecord, OIBannerStateRecord, StrikeOISnapshotRecord, OISnapshotRecord, OIDayBaselineRecord, OIBannerSettingRecord):
                await session.execute(delete(model))
            await session.commit()
    _run(go())
    yield


def _view(**over):
    base = dict(direction="NEUTRAL", stable_direction="BULLISH", stable_strength="Strong", pcr_band="SIDEWAYS", max_pain=24500.0,
                max_pain_ref=24500.0, dte=5, wall=None)
    base.update(over)
    return StateView(**base)


def _types(events):
    return [e.alert_type for e in events]


def test_detect_raises_only_real_changes():
    s = OIAlertSettings(enabled=True)
    assert oi_alerts.detect(_view(), _view(), s, 50) == []                                    # nothing changed
    assert _types(oi_alerts.detect(_view(), _view(direction="BULLISH"), s, 50)) == ["DIRECTION_CHANGE"]
    flip = oi_alerts.detect(_view(), _view(stable_direction="BEARISH", stable_strength="Strong"), s, 50)
    assert _types(flip) == ["STABLE_FLIP"] and (flip[0].old_state, flip[0].new_state) == ("BULLISH", "BEARISH")
    assert _types(oi_alerts.detect(_view(), _view(stable_strength="Weakening"), s, 50)) == ["STRENGTH_CHANGE"]
    band = oi_alerts.detect(_view(), _view(pcr_band="OVERBOUGHT"), s, 50)
    assert _types(band) == ["PCR_BAND"] and band[0].severity == NotificationSeverity.WARNING
    assert oi_alerts.detect(_view(), _view(pcr_band="MILD_BULLISH"), s, 50)[0].severity == NotificationSeverity.INFO
    assert oi_alerts.detect(_view(), _view(max_pain=24550.0), s, 50) == []                      # one strike: under 2
    assert _types(oi_alerts.detect(_view(), _view(max_pain=24600.0), s, 50)) == ["MAX_PAIN_MOVE"]
    assert _types(oi_alerts.detect(_view(max_pain=24550.0, max_pain_ref=24500.0), _view(max_pain=24600.0), s, 50)) == ["MAX_PAIN_MOVE"]
    formed = oi_alerts.detect(_view(), _view(wall="CE@25000"), s, 50)
    assert formed[0].new_state == "formed CE@25000"
    assert oi_alerts.detect(_view(wall="CE@25000"), _view(), s, 50)[0].new_state == "broken CE@25000"
    assert _types(oi_alerts.detect(_view(dte=2), _view(dte=1), s, 50)) == ["DTE"]
    assert oi_alerts.detect(_view(dte=3), _view(dte=2), s, 50) == []
    assert _types(oi_alerts.detect(None, _view(dte=0, direction="BEARISH"), s, 50)) == ["DTE"]   # first slot: no comparison
    only = OIAlertSettings(enabled=True, types=["PCR_BAND"])
    assert _types(oi_alerts.detect(_view(), _view(direction="BULLISH", pcr_band="OVERSOLD"), only, 50)) == ["PCR_BAND"]


def test_quiet_hours_including_overnight():
    day = OIAlertSettings(quiet_start="12:00", quiet_end="13:00")
    ist = oi_alerts.IST
    assert oi_alerts.in_quiet_hours(datetime(2026, 3, 10, 12, 30, tzinfo=ist), day)
    assert not oi_alerts.in_quiet_hours(datetime(2026, 3, 10, 13, 0, tzinfo=ist), day)
    night = OIAlertSettings(quiet_start="22:00", quiet_end="07:00")
    assert oi_alerts.in_quiet_hours(datetime(2026, 3, 10, 23, 0, tzinfo=ist), night) and oi_alerts.in_quiet_hours(datetime(2026, 3, 10, 6, 0, tzinfo=ist), night)
    assert not oi_alerts.in_quiet_hours(datetime(2026, 3, 10, 9, 0, tzinfo=ist), night)
    assert not oi_alerts.in_quiet_hours(datetime(2026, 3, 10, 9, 0, tzinfo=ist), OIAlertSettings())


def _banner_state():
    chain = _chain()
    totals = oi_regime.summarise_chain(chain, 50, 6)
    return oi_regime.evaluate_snapshot(UND, totals, totals, [], oi_regime.OIRegimeSettings(), max_pain_strike=24500, expiry=chain.expiry, today=date(2026, 3, 10))


def test_render_follows_the_template_and_never_words_an_order():
    state = _banner_state()
    event = oi_alerts.AlertEvent("DIRECTION_CHANGE", "BULLISH", "MIXED", NotificationSeverity.INFO)
    r = oi_alerts.render(UND, event, state, T0, T0 + timedelta(seconds=12))
    assert r["message"].startswith(f"{UND} OI banner: BULLISH → MIXED. ") and "Put: " in r["message"] and "PCR 2 (" in r["message"]
    assert "Max pain 24,500" in r["message"] and "DTE 16" in r["message"] and r["message"].endswith("Data as of 10 Mar 09:20 IST.")
    assert r["payload"]["schema"] == "oi_banner.v1" and r["payload"]["data_timestamps"]["slot"].startswith("2026-03-10T03:50")
    assert set(r["payload"]) >= {"underlying", "alert_type", "old_state", "new_state", "snapshot", "data_timestamps"}
    for kind in oi_regime.ALERT_TYPES:
        text = json.dumps(oi_alerts.render(UND, oi_alerts.AlertEvent(kind, "A", "B", NotificationSeverity.INFO), state, T0, T0))
        assert not re.search(r"\b(buy|sell|target|recommended)\b", text, re.I)


def _enable(headers, **alerts):
    body = {"enabled": True, "overrides": {"alerts": {"enabled": True, "cooldown_minutes": 0, **alerts}}}
    assert client.put(f"/api/option-chain/{UND}/settings", json=body, headers=headers).status_code == 200


async def _evaluate(tenant_id, when, market_open=True):
    async with _session_factory() as session:
        return await oi_alerts.evaluate_tenant(session, tenant_id, UND, when, market_open=market_open)


async def _notes(tenant_id):
    async with _session_factory() as session:
        return list(await session.scalars(select(NotificationRecord).where(NotificationRecord.tenant_id == tenant_id,
                                                                           NotificationRecord.event_type.in_(["OI_BANNER", "OI_COLLECTOR"]))
                                          .order_by(NotificationRecord.id)))


def test_a_change_notifies_once_and_a_repeat_of_the_slot_does_not():
    headers, me = _owner("o4-change@example.com")
    _enable(headers, types=["DIRECTION_CHANGE"])
    _run(_collect(_chain(), T0))
    assert _run(_evaluate(me["tenant_id"], T0 + timedelta(seconds=30))) == []              # first slot: state stored only
    _run(_collect(_chain(put=2200.0, put_ltp=9.5), T0 + timedelta(minutes=5)))             # put writing -> BULLISH
    assert _run(_evaluate(me["tenant_id"], T0 + timedelta(minutes=5, seconds=30))) == ["SENT"]
    assert _run(_evaluate(me["tenant_id"], T0 + timedelta(minutes=6))) == []                # same slot again
    notes = _run(_notes(me["tenant_id"]))
    assert len(notes) == 1 and notes[0].event_type == "OI_BANNER" and "NEUTRAL → BULLISH" in notes[0].title
    payload = json.loads(notes[0].metadata_json)
    assert payload["alert_type"] == "DIRECTION_CHANGE" and payload["snapshot"]["direction"] == "BULLISH"
    other_headers, other = _owner("o4-change-off@example.com")                               # alerts off: nothing
    assert _run(_evaluate(other["tenant_id"], T0 + timedelta(minutes=5, seconds=30))) == []


def test_cooldown_snooze_and_closed_market_hold_alerts_back():
    headers, me = _owner("o4-hold@example.com")
    _enable(headers, types=["DIRECTION_CHANGE"], cooldown_minutes=60)
    puts = [2000.0, 2200.0, 2200.0, 2420.0]
    put_ltp = [10.0, 9.5, 10.5, 9.0]
    calls = [1000.0, 1000.0, 1100.0, 1000.0]
    call_ltp = [10.0, 10.0, 9.4, 10.0]
    results = []
    for i in range(4):
        _run(_collect(_chain(put=puts[i], put_ltp=put_ltp[i], call=calls[i], call_ltp=call_ltp[i]), T0 + timedelta(minutes=5 * i)))
        results.append(_run(_evaluate(me["tenant_id"], T0 + timedelta(minutes=5 * i, seconds=30))))
    assert results[1] == ["SENT"] and "COOLDOWN" in results[2] + results[3]
    assert len(_run(_notes(me["tenant_id"]))) == 1
    assert client.post(f"/api/option-chain/{UND}/alerts/snooze", json={"minutes": 30}, headers=headers).json()["snoozed_until"]
    log = client.get(f"/api/option-chain/{UND}/alerts", params={"date": "2026-03-10"}, headers=headers).json()
    assert log["snoozed_until"] and {a["status"] for a in log["alerts"]} >= {"SENT", "COOLDOWN"}
    assert client.post(f"/api/option-chain/{UND}/alerts/resume", headers=headers).json()["snoozed_until"] is None
    assert client.post(f"/api/option-chain/{UND}/alerts/mute-today", headers=headers).json()["snoozed_until"].endswith("+00:00")
    assert _run(_evaluate(me["tenant_id"], T0 + timedelta(minutes=30), market_open=False)) == []

    async def demote():
        async with _session_factory() as session:
            from app.db.models import User
            user = await session.get(User, me["id"])
            user.role = "VIEWER"
            await session.commit()
    _run(demote())
    assert client.post(f"/api/option-chain/{UND}/alerts/snooze", json={"minutes": 30}, headers=headers).status_code == 403


def test_snoozed_alerts_are_logged_not_sent():
    headers, me = _owner("o4-snooze@example.com")
    _enable(headers, types=["DIRECTION_CHANGE"])
    client.post(f"/api/option-chain/{UND}/alerts/snooze", json={"minutes": 600}, headers=headers)

    async def snooze_at(until):
        async with _session_factory() as session:
            row = await session.scalar(select(OIBannerSettingRecord).where(OIBannerSettingRecord.tenant_id == me["tenant_id"], OIBannerSettingRecord.underlying == UND))
            row.snoozed_until = until
            await session.commit()
    _run(snooze_at(T0 + timedelta(hours=1)))                                                   # snoozed relative to the test clock
    _run(_collect(_chain(), T0))
    _run(_evaluate(me["tenant_id"], T0 + timedelta(seconds=30)))
    _run(_collect(_chain(put=2200.0, put_ltp=9.5), T0 + timedelta(minutes=5)))
    assert _run(_evaluate(me["tenant_id"], T0 + timedelta(minutes=5, seconds=30))) == ["SNOOZED"]
    assert _run(_notes(me["tenant_id"])) == []


def test_a_stale_collector_raises_one_ops_alert_per_day():
    headers, me = _owner("o4-stale@example.com")
    _enable(headers, types=["COLLECTOR_STALE"])
    _run(_collect(_chain(), T0))
    later = T0 + timedelta(minutes=40)
    assert _run(_evaluate(me["tenant_id"], later)) == ["SENT"]
    assert _run(_evaluate(me["tenant_id"], later + timedelta(minutes=10))) == ["DUPLICATE"]
    notes = _run(_notes(me["tenant_id"]))
    assert len(notes) == 1 and notes[0].event_type == "OI_COLLECTOR" and notes[0].severity == "WARNING"

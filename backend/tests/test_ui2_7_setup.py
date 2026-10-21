"""UI2-7: the setup wizard's progress - the `setup` preferences namespace and `GET/PUT /api/me/setup` (the stored marks
next to each step's "done", worked out from the caller's own rows): defaults, versions and 409, schema validation,
isolation per user and per organisation, the steps' evidence (PAPER only), reads only, and no flag needed."""
import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.db.models import (
    AlertChannelRecord, BacktestRunRecord, BrokerCredentialRecord, NotificationLinkRecord, PaperAccountRecord, RiskSettingsRecord,
    StrategyDeploymentRecord, UserPrefsRecord,
)
from app.me import prefs, setup
from tests.test_auth_api import _session_factory, client
from tests.test_ui2_4a1_prefs import _invite, _who

BASE = "/api/me/setup"
NO_MARK = {"skipped_at": None}
SETUP_DEFAULTS = {"steps": {k: NO_MARK for k in ("broker", "risk", "strategy", "alerts")}, "opened_at": None, "dismissed_at": None}
T = "2026-10-21T09:30:00Z"


def _run(coro):
    return asyncio.run(coro)


def _put(who: dict, body, path: str = BASE, **params):
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    return client.put(path, headers={**who["headers"], "Content-Type": "application/json"}, content=data, params=params)


def _get(who: dict) -> dict:
    got = client.get(BASE, headers=who["headers"])
    assert got.status_code == 200, got.text
    return got.json()


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(list(rows))
            await session.commit()
    _run(go())


def _skipped(*keys: str) -> dict:
    return {**SETUP_DEFAULTS, "steps": {k: ({"skipped_at": T} if k in keys else NO_MARK) for k in setup.STEPS}}


def test_defaults_before_anything_is_saved():
    who = _who("setup-defaults")
    got = _get(who)
    assert got == {
        "steps": [{"key": k, "done": False, "skipped": False, "skipped_at": None} for k in ("broker", "risk", "strategy", "alerts")],
        "done_count": 0, "next": "broker", "complete": False, "stored": SETUP_DEFAULTS, "version": 0,
    }
    # the same namespace through the generic preferences
    all_prefs = client.get("/api/me/prefs", headers=who["headers"]).json()
    assert all_prefs["prefs"]["setup"] == SETUP_DEFAULTS and all_prefs["versions"]["setup"] == 0
    assert prefs.defaults("setup") == SETUP_DEFAULTS


def test_skip_and_resume_with_versions():
    who = _who("setup-skip")
    saved = _put(who, _skipped("broker"), version=0)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["version"] == 1 and body["next"] == "risk" and body["complete"] is False
    assert body["steps"][0] == {"key": "broker", "done": False, "skipped": True, "skipped_at": "2026-10-21T09:30:00Z"}
    # resumable: another device reads the same marks
    assert _get(who)["next"] == "risk"
    # the generic route and the alias are one store (one version line)
    assert client.get("/api/me/prefs", headers=who["headers"]).json()["versions"]["setup"] == 1
    again = _put(who, _skipped("broker", "risk", "strategy", "alerts"), path="/api/me/prefs/setup", version=1)
    assert again.status_code == 200 and again.json()["version"] == 2
    done = _get(who)
    assert done["complete"] is True and done["next"] is None and done["version"] == 2


def test_stale_version_is_a_409_with_the_current_value():
    who = _who("setup-cas")
    assert _put(who, {**SETUP_DEFAULTS, "opened_at": T}, version=0).status_code == 200
    stale = _put(who, {**SETUP_DEFAULTS, "dismissed_at": T}, version=0)
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["version"] == 1 and detail["current"]["opened_at"] == "2026-10-21T09:30:00Z" and detail["current"]["dismissed_at"] is None
    assert _put(who, {**SETUP_DEFAULTS, "dismissed_at": T}).status_code == 200      # without a version the save replaces
    assert _get(who)["stored"]["dismissed_at"] == "2026-10-21T09:30:00Z"


@pytest.mark.parametrize("body", [
    {"steps": {"broker": {"skipped_at": "yesterday"}}},
    {"steps": {"broker": {"skipped_at": 1}}},
    {"steps": {"broker": {"done_at": T}}},              # done is never stored
    {"steps": {"live": {"skipped_at": T}}},
    {"steps": {"broker": True}},
    {"opened_at": True},
    {"dismissed": True},
    ["steps"],
    "not json",
    "",
])
def test_schema_validation(body):
    who = _who("setup-bad")
    for path in (BASE, "/api/me/prefs/setup"):
        response = _put(who, body, path=path)
        assert response.status_code == 422, (path, response.text)
    assert _get(who)["version"] == 0


def test_size_cap():
    who = _who("setup-size")
    response = _put(who, json.dumps(SETUP_DEFAULTS) + " " * prefs.MAX_BODY_BYTES)
    assert response.status_code == 413
    assert _get(who)["version"] == 0


def test_anonymous_is_refused():
    assert client.get(BASE).status_code == 401
    assert client.put(BASE, json=SETUP_DEFAULTS).status_code == 401


def test_per_user_and_per_organisation():
    owner, other_org = _who("setup-iso-a"), _who("setup-iso-b")
    member = _invite(owner, "USER")
    assert _put(owner, {**_skipped("broker"), "dismissed_at": T}).status_code == 200
    for who in (member, other_org):
        got = _get(who)
        assert got["stored"] == SETUP_DEFAULTS and got["version"] == 0 and got["next"] == "broker", who
    assert _put(member, _skipped("risk")).json()["version"] == 1
    mine = _get(owner)
    assert mine["stored"]["dismissed_at"] == "2026-10-21T09:30:00Z" and mine["steps"][1]["skipped"] is False


def test_a_viewer_may_save_their_own():
    viewer = _invite(_who("setup-viewer"), "VIEWER")
    assert _put(viewer, _skipped("strategy")).status_code == 200
    assert _get(viewer)["steps"][2]["skipped"] is True


def test_no_flag_needed_like_the_other_preferences():
    """`ui_v2` is off by default; the trader's own progress reads and saves all the same (only the v2 screens use it)."""
    from app.platform.controls import DEFAULT_ON_FLAGS
    assert "ui_v2" not in DEFAULT_ON_FLAGS
    who = _who("setup-flag")
    assert _put(who, _skipped("alerts")).status_code == 200
    assert _get(who)["steps"][3]["skipped"] is True


# --- steps worked out from the caller's own rows ----------------------------------------------------------------------

def _broker(tenant_id: int, user_id: int) -> BrokerCredentialRecord:
    return BrokerCredentialRecord(tenant_id=tenant_id, user_id=user_id, broker_name="upstox", account_label="primary", encrypted_payload="x")


def _deployment(tenant_id: int, user_id: int, mode: str) -> StrategyDeploymentRecord:
    return StrategyDeploymentRecord(tenant_id=tenant_id, created_by=user_id, strategy_id="orb_15m_5m", symbol=f"S{uuid.uuid4().hex[:6]}", mode=mode)


def _channel(tenant_id: int, **kw) -> AlertChannelRecord:
    return AlertChannelRecord(tenant_id=tenant_id, channel_type=kw.pop("channel_type", "EMAIL"), encrypted_config="x", **kw)


def _done(who: dict) -> dict:
    return {s["key"]: s["done"] for s in _get(who)["steps"]}


def test_broker_step_is_done_by_a_stored_credential_of_the_organisation():
    owner, other_org = _who("setup-broker-a"), _who("setup-broker-b")
    member = _invite(owner, "USER")
    _add(_broker(other_org["tenant_id"], other_org["user_id"]))
    assert _done(owner)["broker"] is False
    _add(_broker(owner["tenant_id"], owner["user_id"]))
    assert _done(owner)["broker"] is True and _done(member)["broker"] is True       # the account belongs to the organisation
    got = _get(owner)
    assert got["next"] == "risk" and got["done_count"] == 1


def test_a_done_step_is_never_shown_as_skipped_and_reopens_when_its_evidence_goes():
    who = _who("setup-reopen")
    _put(who, _skipped("broker"))
    _add(_broker(who["tenant_id"], who["user_id"]))
    assert _get(who)["steps"][0] == {"key": "broker", "done": True, "skipped": False, "skipped_at": "2026-10-21T09:30:00Z"}

    async def remove():
        async with _session_factory() as session:
            row = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == who["tenant_id"]))
            await session.delete(row)
            await session.commit()
    _run(remove())
    assert _get(who)["steps"][0]["done"] is False


def test_risk_step_is_done_by_the_traders_own_limits_or_the_organisations_settings():
    owner = _who("setup-risk")
    member = _invite(owner, "USER")
    _add(PaperAccountRecord(tenant_id=owner["tenant_id"], user_id=member["user_id"], starting_capital=100000, risk_settings_json="{}"))
    assert _done(member)["risk"] is False                                            # an account with no limits saved
    _add(PaperAccountRecord(tenant_id=owner["tenant_id"], user_id=owner["user_id"], starting_capital=100000,
                            risk_settings_json=json.dumps({"risk_per_trade_pct": 1.0, "daily_loss_pct": 3.0})))
    assert _done(owner)["risk"] is True and _done(member)["risk"] is False           # the owner's own, not the member's
    _add(RiskSettingsRecord(tenant_id=owner["tenant_id"], updated_by=owner["user_id"]))
    assert _done(member)["risk"] is True                                             # the Risk page's settings


def test_strategy_step_counts_only_the_traders_own_paper_deployment_or_backtest():
    owner = _who("setup-strategy")
    member = _invite(owner, "USER")
    _add(_deployment(owner["tenant_id"], owner["user_id"], "LIVE"))
    assert _done(owner)["strategy"] is False                                         # LIVE never counts here
    _add(_deployment(owner["tenant_id"], owner["user_id"], "PAPER"))
    assert _done(owner)["strategy"] is True and _done(member)["strategy"] is False
    _add(BacktestRunRecord(tenant_id=owner["tenant_id"], user_id=member["user_id"], strategy_id="orb_15m_5m", symbol="TEST", base_timeframe="5m"))
    assert _done(member)["strategy"] is True


def test_alerts_step_needs_a_channel_that_delivered_or_the_traders_telegram_link():
    owner = _who("setup-alerts")
    member = _invite(owner, "USER")
    now = datetime.now(timezone.utc)
    _add(_channel(owner["tenant_id"], channel_type="EMAIL", enabled=True))                                 # never sent
    _add(_channel(owner["tenant_id"], channel_type="WEBHOOK", enabled=False, last_delivered_at=now))       # switched off
    _add(_channel(owner["tenant_id"], channel_type="TELEGRAM", enabled=True, last_delivered_at=now, last_error="401 Unauthorized"))
    assert _done(owner)["alerts"] is False
    _add(NotificationLinkRecord(tenant_id=owner["tenant_id"], user_id=member["user_id"], status="pending"))
    assert _done(member)["alerts"] is False
    _add(NotificationLinkRecord(tenant_id=owner["tenant_id"], user_id=member["user_id"], status="linked", linked_at=now))
    assert _done(member)["alerts"] is True and _done(owner)["alerts"] is False
    _add(_channel(owner["tenant_id"], channel_type="PUSH", enabled=True, last_delivered_at=now))           # a test went through
    assert _done(owner)["alerts"] is True


def test_every_step_done_completes_setup_whatever_was_skipped():
    who = _who("setup-all")
    now = datetime.now(timezone.utc)
    _add(_broker(who["tenant_id"], who["user_id"]), RiskSettingsRecord(tenant_id=who["tenant_id"]),
         _deployment(who["tenant_id"], who["user_id"], "PAPER"), _channel(who["tenant_id"], enabled=True, last_delivered_at=now))
    got = _get(who)
    assert got["done_count"] == 4 and got["complete"] is True and got["next"] is None


def test_reading_writes_nothing():
    who = _who("setup-reads")

    async def counts():
        async with _session_factory() as session:
            out = {}
            for model in (PaperAccountRecord, RiskSettingsRecord, StrategyDeploymentRecord, BacktestRunRecord, AlertChannelRecord,
                          BrokerCredentialRecord, NotificationLinkRecord, UserPrefsRecord):
                out[model.__name__] = await session.scalar(select(func.count()).select_from(model).where(model.tenant_id == who["tenant_id"]))
            return out
    before = _run(counts())
    _get(who)
    assert _run(counts()) == before
    _put(who, _skipped("broker"))
    after = _run(counts())
    assert after == {**before, "UserPrefsRecord": before["UserPrefsRecord"] + 1}      # only the trader's own marks


def test_stored_value_reads_leniently():
    assert prefs.stored_value("setup", None) == SETUP_DEFAULTS
    assert prefs.stored_value("setup", json.dumps({"steps": {"broker": {"skipped_at": "nonsense"}}})) == SETUP_DEFAULTS
    newer = json.dumps({"opened_at": T, "intro_dismissed": ["dashboard"]})       # a key a newer release saved is dropped
    assert prefs.stored_value("setup", newer)["opened_at"] == "2026-10-21T09:30:00Z"

"""P3 (a simple front page): the Dashboard's "Get started" card in `GET /api/me/setup` - three steps (broker, paper bot,
Telegram) worked out from the caller's own and their organisation's rows, an Upstox Analytics Token counting as a broker
for PAPER, the card hidden for good (`get_started_hidden_at`) and brought back from Help, bounded marks, and tenancy."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.brokers import upstox_analytics as ua
from app.brokers.models import BrokerCredentials
from app.db.models import AlertChannelRecord, BrokerCredentialRecord, StrategyDeploymentRecord
from app.me import prefs, setup
from app.secrets_store.encryption import encrypt_text
from app.secrets_store.envelope import PURPOSE_BROKER_CREDENTIAL
from tests.test_auth_api import _session_factory, client
from tests.test_ui2_4a1_prefs import _invite, _who

BASE = "/api/me/setup"
T = "2026-10-21T09:30:00Z"
# A made-up value: never a real token (not even token-shaped), only something for the probe metadata to describe.
FAKE_ANALYTICS = "p3-test-analytics-value-not-real"
FAR = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _add(*rows):
    async def go():
        async with _session_factory() as session:
            session.add_all(list(rows))
            await session.commit()
    _run(go())


def _get(who: dict) -> dict:
    got = client.get(BASE, headers=who["headers"])
    assert got.status_code == 200, got.text
    return got.json()


def _card(who: dict) -> dict:
    return _get(who)["get_started"]


def _done(who: dict) -> dict:
    return {s["key"]: s["done"] for s in _card(who)["steps"]}


def _put(who: dict, body, path: str = BASE, **params):
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    return client.put(path, headers={**who["headers"], "Content-Type": "application/json"}, content=data, params=params)


def _verified_broker(tenant_id: int, user_id: int, name: str = "zerodha") -> BrokerCredentialRecord:
    return BrokerCredentialRecord(tenant_id=tenant_id, user_id=user_id, broker_name=name, account_label="primary", encrypted_payload="x",
                                  last_verified_at=datetime.now(timezone.utc))


def _analytics_row(tenant_id: int, user_id: int, coverage: dict, expires: datetime = FAR, broker: str = "upstox",
                   label: str = "primary") -> BrokerCredentialRecord:
    """An Upstox credential that holds only an analytics token (no daily login, never verified)."""
    meta = ua.new_meta(FAKE_ANALYTICS)
    meta["expires_at"] = expires.isoformat()
    meta["coverage"].update({c: {"status": s} for c, s in coverage.items()})
    creds = BrokerCredentials(api_key="made-up-key", analytics_token=FAKE_ANALYTICS, analytics_meta=meta)
    return BrokerCredentialRecord(tenant_id=tenant_id, user_id=user_id, broker_name=broker, account_label=label, token_status="UNKNOWN",
                                  encrypted_payload=encrypt_text(creds.model_dump_json(), tenant_id, PURPOSE_BROKER_CREDENTIAL))


def _deployment(tenant_id: int, user_id: int, mode: str = "PAPER", status: str = "ACTIVE") -> StrategyDeploymentRecord:
    return StrategyDeploymentRecord(tenant_id=tenant_id, created_by=user_id, strategy_id="orb_15m_5m", symbol=f"S{uuid.uuid4().hex[:6]}",
                                    mode=mode, status=status)


def _channel(tenant_id: int, channel_type: str = "TELEGRAM", **kw) -> AlertChannelRecord:
    kw.setdefault("enabled", True)
    return AlertChannelRecord(tenant_id=tenant_id, channel_type=channel_type, encrypted_config="x", **kw)


def _all_done(who: dict) -> None:
    now = datetime.now(timezone.utc)
    _add(_verified_broker(who["tenant_id"], who["user_id"]), _deployment(who["tenant_id"], who["user_id"]),
         _channel(who["tenant_id"], last_delivered_at=now))


# --- the three steps --------------------------------------------------------------------------------------------------

def test_zero_one_two_and_three_steps_done():
    who = _who("p3-states")
    card = _card(who)
    assert card == {"steps": [{"key": "broker", "done": False}, {"key": "paper_bot", "done": False}, {"key": "telegram", "done": False}],
                    "done_count": 0, "complete": False, "hidden_at": None, "shown_at": None, "visible": True}
    _add(_verified_broker(who["tenant_id"], who["user_id"]))
    card = _card(who)
    assert (card["done_count"], card["complete"], card["visible"]) == (1, False, True) and _done(who)["broker"] is True
    _add(_deployment(who["tenant_id"], who["user_id"]))
    card = _card(who)
    assert (card["done_count"], card["complete"], card["visible"]) == (2, False, True) and _done(who)["paper_bot"] is True
    _add(_channel(who["tenant_id"], last_delivered_at=datetime.now(timezone.utc)))
    card = _card(who)
    assert (card["done_count"], card["complete"], card["visible"]) == (3, True, False)      # all done: the card is gone
    assert [s["key"] for s in card["steps"]] == list(setup.GET_STARTED)


def test_the_steps_are_independent_of_order():
    who = _who("p3-order")
    _add(_channel(who["tenant_id"], last_delivered_at=datetime.now(timezone.utc)))
    assert _done(who) == {"broker": False, "paper_bot": False, "telegram": True}
    assert _card(who)["done_count"] == 1 and _card(who)["visible"] is True


def test_paper_bot_is_the_traders_own_paper_deployment_only():
    owner, other_org = _who("p3-bot-a"), _who("p3-bot-b")
    member = _invite(owner, "USER")
    _add(_deployment(owner["tenant_id"], owner["user_id"], mode="LIVE"))
    _add(_deployment(other_org["tenant_id"], other_org["user_id"]))
    assert _done(owner)["paper_bot"] is False                               # LIVE never counts; another organisation's neither
    _add(_deployment(owner["tenant_id"], owner["user_id"], status="STOPPED"))
    assert _done(owner)["paper_bot"] is True                                # a stopped PAPER deployment was still started
    assert _done(member)["paper_bot"] is False                              # the owner's bot is not the member's


def test_telegram_needs_an_enabled_telegram_channel_whose_last_send_went_through():
    owner, other_org = _who("p3-tg-a"), _who("p3-tg-b")
    member = _invite(owner, "USER")
    now = datetime.now(timezone.utc)
    _add(_channel(other_org["tenant_id"], last_delivered_at=now))
    _add(_channel(owner["tenant_id"], channel_type="EMAIL", last_delivered_at=now))               # delivered, but not Telegram
    assert _done(owner)["telegram"] is False
    assert _get(owner)["steps"][3] == {"key": "alerts", "done": True, "skipped": False, "skipped_at": None}   # the wizard's step is

    tg = _channel(owner["tenant_id"], enabled=True)                                               # saved, never sent
    _add(tg)
    assert _done(owner)["telegram"] is False

    async def change(**values):
        async with _session_factory() as session:
            row = await session.get(AlertChannelRecord, tg.id)
            for k, v in values.items():
                setattr(row, k, v)
            await session.commit()
    _run(change(last_delivered_at=now, last_error="401 Unauthorized"))                           # the last send failed
    assert _done(owner)["telegram"] is False
    _run(change(last_error=None, enabled=False))                                                  # switched off
    assert _done(owner)["telegram"] is False
    _run(change(enabled=True))
    assert _done(owner)["telegram"] is True and _done(member)["telegram"] is True                # the organisation's channel
    assert _done(other_org)["telegram"] is True and _done(_who("p3-tg-c"))["telegram"] is False


# --- an Upstox Analytics Token counts as a broker for PAPER -----------------------------------------------------------

def test_an_analytics_token_that_serves_candles_counts_as_a_broker():
    owner, other_org = _who("p3-at-a"), _who("p3-at-b")
    member = _invite(owner, "USER")
    _add(_analytics_row(other_org["tenant_id"], other_org["user_id"], {ua.HISTORICAL: ua.WORKS}))
    assert _done(owner)["broker"] is False                                  # another organisation's token is not ours
    _add(_analytics_row(owner["tenant_id"], owner["user_id"], {ua.HISTORICAL: ua.WORKS, ua.QUOTES: ua.WORKS}))
    assert _done(owner)["broker"] is True and _done(member)["broker"] is True
    assert _get(owner)["steps"][0]["done"] is True                          # the wizard's broker step reads the same rule
    assert _done(other_org)["broker"] is True


@pytest.mark.parametrize("coverage, expires", [
    ({}, FAR),                                                              # saved, never probed: nothing proven
    ({ua.HISTORICAL: ua.REFUSED, ua.INTRADAY: ua.REFUSED}, FAR),            # the broker refused it
    ({ua.WEBSOCKET: ua.WORKS, ua.QUOTES: ua.WORKS}, FAR),                   # no candles: a paper bot could not run
    ({ua.HISTORICAL: ua.WORKS}, datetime.now(timezone.utc) - timedelta(days=1)),   # expired
])
def test_an_analytics_token_that_cannot_serve_candles_does_not_count(coverage, expires):
    who = _who("p3-at-no")
    _add(_analytics_row(who["tenant_id"], who["user_id"], coverage, expires))
    assert _done(who)["broker"] is False


def test_only_upstox_rows_are_read_for_the_token_and_an_unreadable_row_is_skipped():
    who = _who("p3-at-other")
    _add(_analytics_row(who["tenant_id"], who["user_id"], {ua.HISTORICAL: ua.WORKS}, broker="fyers"))
    _add(BrokerCredentialRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], broker_name="upstox", account_label="broken",
                                encrypted_payload="not-a-ciphertext"))
    assert _done(who)["broker"] is False                                    # never a 500, never a guess
    _add(_analytics_row(who["tenant_id"], who["user_id"], {ua.INTRADAY: ua.WORKS}, label="second"))
    assert _done(who)["broker"] is True


def test_the_answer_never_carries_the_token():
    who = _who("p3-at-secret")
    _add(_analytics_row(who["tenant_id"], who["user_id"], {ua.HISTORICAL: ua.WORKS}))
    raw = client.get(BASE, headers=who["headers"]).text
    assert FAKE_ANALYTICS not in raw and "analytics" not in raw


# --- hidden for good, and back from Help --------------------------------------------------------------------------------

def _marks(**kw) -> dict:
    return {**prefs.defaults("setup"), **kw}


def test_hidden_for_good_stays_hidden_even_when_a_step_opens_again():
    who = _who("p3-hide")
    assert _put(who, _marks(get_started_hidden_at=T)).status_code == 200                       # "Hide" with nothing done
    card = _card(who)
    assert card["visible"] is False and card["hidden_at"] == "2026-10-21T09:30:00Z" and card["complete"] is False
    _all_done(who)
    assert _card(who)["visible"] is False

    async def remove_broker():
        async with _session_factory() as session:
            row = await session.scalar(select(BrokerCredentialRecord).where(BrokerCredentialRecord.tenant_id == who["tenant_id"]))
            await session.delete(row)
            await session.commit()
    _run(remove_broker())
    card = _card(who)
    assert card["complete"] is False and card["visible"] is False                               # gone for good


def test_complete_without_marks_is_not_shown_and_help_brings_it_back():
    who = _who("p3-help")
    _all_done(who)
    assert _card(who)["visible"] is False
    # the app stores the "for good" mark once it sees every step done
    saved = _put(who, _marks(get_started_hidden_at=T), version=0)
    assert saved.status_code == 200 and saved.json()["get_started"]["visible"] is False
    # Help -> "Show Get started" with every step done: the mark cleared, shown_at set; the card shows its three ticks
    back = _put(who, _marks(get_started_shown_at=T), version=1)
    assert back.status_code == 200
    card = back.json()["get_started"]
    assert card["visible"] is True and card["complete"] is True and card["done_count"] == 3 and card["shown_at"] == "2026-10-21T09:30:00Z"
    # hidden again (clears shown_at)
    assert _put(who, _marks(get_started_hidden_at=T), version=2).json()["get_started"]["visible"] is False


def test_help_brings_back_an_unfinished_card():
    who = _who("p3-help-open")
    _put(who, _marks(get_started_hidden_at=T))
    assert _card(who)["visible"] is False
    _put(who, _marks())
    assert _card(who)["visible"] is True


def test_the_card_marks_live_beside_the_wizards_marks():
    who = _who("p3-beside")
    body = {**_marks(get_started_hidden_at=T, opened_at=T, dismissed_at=T), "steps": {**prefs.defaults("setup")["steps"], "risk": {"skipped_at": T}}}
    got = _put(who, body).json()
    assert got["stored"]["opened_at"] and got["stored"]["dismissed_at"] and got["stored"]["steps"]["risk"]["skipped_at"]
    assert got["get_started"]["visible"] is False and got["next"] == "broker"
    # the generic preferences route is the same store
    assert client.get("/api/me/prefs", headers=who["headers"]).json()["prefs"]["setup"]["get_started_hidden_at"] == "2026-10-21T09:30:00Z"


@pytest.mark.parametrize("field", ["get_started_hidden_at", "get_started_shown_at"])
@pytest.mark.parametrize("bad", [
    "2026-10-21T09:30:00",                     # no time zone
    "2019-12-31T23:59:00Z",                    # before 2020
    "2099-01-01T00:00:00Z",                    # in the future
    "yesterday", 1, True,
])
def test_the_new_marks_are_bounded_like_the_others(field, bad):
    who = _who("p3-bounds")
    for path in (BASE, "/api/me/prefs/setup"):
        response = _put(who, {field: bad}, path=path)
        assert response.status_code == 422, (path, response.text)
        assert bad not in json.dumps(response.json()) if isinstance(bad, str) else True     # the input is never echoed
    assert _get(who)["version"] == 0
    with pytest.raises(ValueError):
        prefs.SetupPrefs.model_validate_json(json.dumps({field: bad}))
    soon = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat()
    assert getattr(prefs.SetupPrefs.model_validate_json(json.dumps({field: soon})), field) is not None    # a clock a little ahead


def test_one_bad_stored_card_mark_is_dropped_on_its_own():
    raw = json.dumps({"get_started_hidden_at": "2019-01-01T00:00:00Z", "get_started_shown_at": T, "opened_at": T})
    read = prefs.stored_value("setup", raw)
    assert read["get_started_hidden_at"] is None and read["get_started_shown_at"] == "2026-10-21T09:30:00Z"
    assert read["opened_at"] == "2026-10-21T09:30:00Z"


# --- tenancy ------------------------------------------------------------------------------------------------------------

def test_the_card_marks_are_per_user_and_per_organisation():
    owner, other_org = _who("p3-iso-a"), _who("p3-iso-b")
    member = _invite(owner, "USER")
    assert _put(owner, _marks(get_started_hidden_at=T)).status_code == 200
    assert _card(owner)["visible"] is False
    for who in (member, other_org):
        card = _card(who)
        assert card["visible"] is True and card["hidden_at"] is None, who
    viewer = _invite(owner, "VIEWER")
    assert _put(viewer, _marks(get_started_hidden_at=T)).status_code == 200                    # anyone may hide their own
    assert _card(owner)["hidden_at"] == "2026-10-21T09:30:00Z" and _card(member)["hidden_at"] is None


def test_get_started_view_rule():
    done = {"broker": True, "paper_bot": True, "telegram": True}
    none = {"get_started_hidden_at": None, "get_started_shown_at": None}
    assert setup.get_started_view(none, done)["visible"] is False
    assert setup.get_started_view({**none, "get_started_shown_at": T}, done)["visible"] is True
    assert setup.get_started_view({"get_started_hidden_at": T, "get_started_shown_at": T}, done)["visible"] is False
    assert setup.get_started_view(none, {**done, "telegram": False})["visible"] is True
    assert setup.get_started_view({}, {})["visible"] is True                                   # an old stored value

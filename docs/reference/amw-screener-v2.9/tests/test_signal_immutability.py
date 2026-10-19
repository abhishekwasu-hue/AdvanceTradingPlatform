"""A-005: the original signal record is immutable; later qualification / invalidation / expiry are append-only
lifecycle events traced to it; no revised duplicate record; retries create no duplicate events or notifications;
history, API and report workflows stay compatible."""
import json

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DatabaseError

from src.services import notify_events as NE
from src.services.lifecycle import update_lifecycle
from src.storage.db import Store, signal_events, signals


def row(status="candidate", key="a005key000000001", score=55.0):
    return {"key": key, "ticker": "A.NS", "symbol": "A", "strategy": "breakout", "strategy_version": 3, "setup": "Breakout",
            "timeframe": "1D", "direction": "bullish", "signal_time": "2026-10-15 00:00", "data_time": "2026-10-15 00:00",
            "status": status, "label": "Watch", "score": score, "rr": 2.0, "stop": 90.0, "target": 120.0,
            "expiry": "3 completed 1D bars", "data_snapshot": "snap-1", "provider": "fixture"}


ORIGINAL = Store.IMMUTABLE_SIGNAL_COLUMNS


def snapshot(store, key):
    with store.engine.connect() as c:
        r = c.execute(select(signals).where(signals.c.key == key)).first()._mapping
    return {k: (json.dumps(r[k], sort_keys=True) if k == "payload" else r[k]) for k in ORIGINAL}


@pytest.fixture
def store(tmp_path):
    s = Store(f"sqlite:///{tmp_path}/a5.db")
    s.uid = s.ensure_local_user()
    s.record_signals([row()], s.uid)
    s.key = s.scoped_key(s.uid, "a005key000000001")
    return s


def test_qualification_is_an_event_and_the_original_record_is_unchanged(store):
    before = snapshot(store, store.key)
    assert store.get_signal(store.key, store.uid)["state"] == "awaiting_confirmation"
    later_qualified = row(status="qualified", score=82.0)
    assert store.record_signals([later_qualified], store.uid) == 0               # no revised duplicate record
    ch = update_lifecycle(store, [later_qualified], {}, store.uid)
    assert ch == [(store.key, "qualified")]
    assert snapshot(store, store.key) == before                                  # original fields untouched
    rec = store.get_signal(store.key, store.uid)
    assert rec["status"] == "candidate" and rec["score"] == 55.0 and rec["state"] == "qualified"
    with store.engine.connect() as c:
        assert c.execute(select(signals).where(signals.c.canonical_key == "a005key000000001")).all().__len__() == 1
    ev = store.signal_events(store.key, store.uid)
    assert [(e["from_state"], e["to_state"]) for e in ev] == [(None, "awaiting_confirmation"),
                                                               ("awaiting_confirmation", "qualified")]
    assert all(e["key"] == store.key for e in ev)                                 # traceable to the original


def test_reprocessing_and_retries_do_not_duplicate_events(store):
    q = row(status="qualified")
    assert update_lifecycle(store, [q], {}, store.uid) == [(store.key, "qualified")]
    assert update_lifecycle(store, [q], {}, store.uid) == []                    # second pass: nothing new
    assert store.transition(store.key, "qualified", "retry", store.uid) is False   # explicit retry: no-op
    with store.engine.connect() as c:
        n = c.execute(select(signal_events).where(signal_events.c.key == store.key,
                                                  signal_events.c.to_state == "qualified")).all()
    assert len(n) == 1
    with pytest.raises(ValueError):
        store.transition(store.key, "awaiting_confirmation", "backwards", store.uid)   # illegal transitions still rejected


def test_database_rejects_edits_to_original_fields_but_allows_lifecycle_projection(store):
    for col, val in (("payload", {"x": 1}), ("score", 99.0), ("status", "qualified"), ("signal_time", "2026-01-01 00:00"),
                     ("strategy_version", 9), ("first_seen", None)):
        with pytest.raises(DatabaseError, match="immutable"):
            with store.engine.begin() as c:
                c.execute(update(signals).where(signals.c.key == store.key).values(**{col: val}))
    with store.engine.begin() as c:                                              # projection columns may change
        c.execute(update(signals).where(signals.c.key == store.key).values(state_reason="note"))


def test_qualification_notification_quotes_the_original_and_is_not_duplicated(store):
    q = row(status="qualified")
    ch = update_lifecycle(store, [q], {}, store.uid)
    out = NE.on_lifecycle(store, store.uid, ch)
    msg = store.notifications(store.uid)[0]["message"]
    assert out and "lifecycle event" in msg and "QUALIFIED" in msg and "originally recorded as 'candidate'" in msg
    assert NE.on_lifecycle(store, store.uid, ch) == []                          # job retry: no second notification
    assert NE.on_lifecycle(store, store.uid, update_lifecycle(store, [q], {}, store.uid)) == []


def test_report_and_api_distinguish_original_from_events(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import create_app
    from src.data.fixture_provider import FixtureProvider
    from src.reports import builder
    from src.services.app_services import Services
    svc = Services(db_url=f"sqlite:///{tmp_path}/r.db", provider=FixtureProvider(end="2026-10-16"))
    u = svc.local_user
    svc.store.record_signals([row()], u)
    key = svc.store.scoped_key(u, "a005key000000001")
    update_lifecycle(svc.store, [row(status="qualified")], {}, u)
    js = json.loads(builder.generate(svc, u, key, "json")[1])
    assert js["summary"]["original_status"] == "candidate" and js["summary"]["lifecycle_state"] == "qualified"
    assert js["summary"]["status"] == "candidate"                                # existing field kept (compatible)
    assert [(e["from"], e["to"]) for e in js["lifecycle_events"]] == [("awaiting_confirmation", "qualified")]
    csv_text = builder.generate(svc, u, key, "csv")[1].decode("utf-8-sig")
    assert "lifecycle_event,awaiting_confirmation -> qualified" in csv_text
    monkeypatch.setenv("AMW_AUTH_MODE", "local")
    c = TestClient(create_app(svc))
    lst = c.get("/api/v1/signals").json()
    assert len(lst) == 1 and lst[0]["status"] == "candidate" and lst[0]["state"] == "qualified"
    one = c.get(f"/api/v1/signals/{key}").json()
    assert [e["to_state"] for e in one["lifecycle"]] == ["awaiting_confirmation", "qualified"]

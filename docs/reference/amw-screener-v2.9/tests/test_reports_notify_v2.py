"""Research reports (backtest / forward / daily) produce parseable PDFs + CSVs from stored data; system notifications:
canonical-record content, DB duplicate suppression, quiet hours, hourly cap, delivery status only on success,
per-kind opt-in, invalidation hook, feed-failure hook, forward-closed hook."""
import io
import time
from datetime import timedelta

import pandas as pd
import pytest

from src.data.fixture_provider import FixtureProvider
from src.reports import research
from src.services import notify_events as NE
from src.services.app_services import Services
from src.storage.db import utcnow

UNI = pd.DataFrame({"symbol": list("ABCDEFGH"), "ticker": [f"{x}.NS" for x in "ABCDEFGH"], "company": "", "industry": ""})


@pytest.fixture
def svc(tmp_path):
    s = Services(db_url=f"sqlite:///{tmp_path}/rn.db", provider=FixtureProvider(end="2026-10-16"))
    j = s.start_scan(UNI, "t", ["breakout", "pullback", "reversal", "trend_continuation"])
    while j.status in ("queued", "running"):
        time.sleep(0.05)
    return s


def _pdf_ok(b):
    from pypdf import PdfReader  # noqa: F401
    return b[:5] == b"%PDF-"


def test_research_reports_are_generated_from_stored_records(svc):
    from src.backtest.engine import run_backtest
    from src.data.validation import clean_and_validate
    cfg = svc.cfg()
    d, _ = clean_and_validate(svc._provider.fetch_many(["Q.NS"], "1D")["Q.NS"], "Q", "1D", cfg)
    res = run_backtest(d, "1D", cfg, "breakout")
    pdf, csv = research.backtest_report(res, "bt")
    assert pdf[:5] == b"%PDF-" and len(pd.read_csv(io.BytesIO(csv))) == len(res["trades"])
    pdf, csv = research.forward_report(svc.store, svc.local_user)
    assert pdf[:5] == b"%PDF-" and len(pd.read_csv(io.BytesIO(csv))) == len(svc.store.forward_list(svc.local_user))
    day = str(utcnow().date())
    pdf, csv = research.daily_report(svc.store, svc.local_user, day)
    rows = pd.read_csv(io.BytesIO(csv))
    assert pdf[:5] == b"%PDF-" and set(rows.key) == {h["key"] for h in svc.store.signal_history(svc.local_user)}
    other, _ = svc.store.create_user("x")
    assert pd.read_csv(io.BytesIO(research.daily_report(svc.store, other, day)[1])).empty   # tenant-scoped


def test_notifications_dedup_quiet_hours_cap_and_status(svc):
    from src.services.alerts import save_notify
    u = svc.local_user
    sent = []
    senders = {"telegram": lambda uid, t: (sent.append(t), ("sent", None))[1], "email": lambda uid, t: ("failed", "smtp down")}
    NE.save_prefs(svc.store, u, {"daily_summary": ["in_app", "telegram", "email"]})
    out = NE.notify(svc.store, u, "daily_summary", "d1", "hello", senders)
    assert {(o["channel"], o["status"]) for o in out} == {("in_app", "sent"), ("telegram", "sent"), ("email", "failed")}
    assert NE.notify(svc.store, u, "daily_summary", "d1", "hello", senders) == []           # duplicate suppressed by DB
    assert len(sent) == 1
    save_notify(svc.store, u, {"email": None, "quiet_hours": ["00:00", "23:59"], "max_per_hour": 20})
    q = NE.notify(svc.store, u, "daily_summary", "d2", "x", senders)
    assert [o["status"] for o in q if o["channel"] == "telegram"] == ["suppressed"]
    save_notify(svc.store, u, {"email": None, "quiet_hours": None, "max_per_hour": 1})
    c = NE.notify(svc.store, u, "daily_summary", "d3", "x", senders)
    assert [o["error"] for o in c if o["channel"] == "telegram"] == ["rate limit (1/hour)"]
    assert NE.prefs(svc.store, u)["feed_failure"] == ["in_app"]                              # telegram is opt-in
    with pytest.raises(ValueError):
        NE.save_prefs(svc.store, u, {"daily_summary": ["sms"]})
    other, _ = svc.store.create_user("y")
    assert svc.store.notifications(other) == []


def test_invalidation_hook_uses_canonical_record(svc):
    u = svc.local_user
    rec = svc.store.signal_history(u)[0]
    svc.store.transition(rec["key"], "invalidated", "close beyond stop", u)
    out = NE.on_lifecycle(svc.store, u, [(rec["key"], "invalidated")])
    msg = svc.store.notifications(u)[0]["message"]
    assert out and rec["ticker"] in msg and "INVALIDATED" in msg and "close beyond stop" in msg and rec["key"][:8] in msg


def test_daily_summary_and_forward_closed_hooks(svc):
    from src.services.forward import mark
    u = svc.local_user
    txt = NE.daily_summary_text(svc, u, str(utcnow().date()))
    assert txt and "New stored signals" in txt
    svc._provider = FixtureProvider(end="2026-11-20")
    r = mark(svc, u)
    kinds = [n["kind"] for n in svc.store.notifications(u)]
    assert kinds.count("forward_closed") == r["closed"]


def test_scheduler_daily_maintenance_sends_summary_once(svc):
    from src.services.scheduler import Scheduler
    now = pd.Timestamp(f"{utcnow().date()} 16:00", tz="Asia/Kolkata").tz_convert("UTC").tz_localize(None).to_pydatetime()
    sch = Scheduler(svc, clock=lambda: now)
    out = sch.daily_maintenance(now)
    assert out["summaries"] == 1 and sch.daily_maintenance(now + timedelta(minutes=5)) is None

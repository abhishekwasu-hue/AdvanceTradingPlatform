"""AUD-G2.4 (owner audit 28 Oct): activation / time to first value.

Covered: `atp_activation_total{step}` goes up once per person per step (a second call, a second process memory and a
second paper trade do not count again; LIVE is not a paper trade), the only label is the step, signup is counted by the
register form, a paper trade by `persist_trade`, a journal review by opening one trade's journal; every step is wired
where it happens; the funnel math (counts, median hours, first-session share with its open and decided people); the
admin read is SUPER_ADMIN + two-factor only and carries the funnel; the migration chains after the previous head.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import ACTIVATION_FIRST_SESSION_HOURS
from app.core.enums import SignalDirection
from app.core.models import Trade
from app.db.models import ActivationEventRecord, TradeRecord, User
from app.growth import activation
from app.observability.metrics import ACTIVATION
from tests.test_admin_api import _admin
from tests.test_admin_console import _member
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _register, _session_factory, client

run = asyncio.run
UTC = timezone.utc
BACKEND = Path(__file__).resolve().parent.parent


def _count(step: str) -> float:
    return ACTIVATION.labels(step=step)._value.get()  # noqa: SLF001 - the counter's own value


def _who(email: str):
    headers = {"Authorization": f"Bearer {_register(email)}"}
    return headers, client.get("/api/auth/me", headers=headers).json()


def _rows(user_id: int, step: str) -> int:
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(ActivationEventRecord)
                                        .where(ActivationEventRecord.user_id == user_id, ActivationEventRecord.step == step))
    return run(go())


def _record(user_id: int, step: str) -> bool:
    async def go():
        async with _session_factory() as session:
            return await activation.record_step(session, await session.get(User, user_id), step)
    return run(go())


# --- counted once per person per step -----------------------------------------------------------------------------------

def test_signup_is_counted_once_per_person():
    before = _count("signup")
    _, me = _who("g24-signup-a@example.com")
    assert _count("signup") == before + 1 and _rows(me["id"], "signup") == 1
    _, other = _who("g24-signup-b@example.com")
    assert _count("signup") == before + 2 and _rows(other["id"], "signup") == 1
    assert _record(me["id"], "signup") is False                       # the process memory answers
    activation.reset_memory()
    assert _record(me["id"], "signup") is False                       # the database's unique pair answers
    assert _count("signup") == before + 2 and _rows(me["id"], "signup") == 1


def test_each_step_once_and_only_known_steps():
    _, me = _who("g24-steps@example.com")
    for step in activation.STEP_KEYS[1:]:
        before = _count(step)
        assert _record(me["id"], step) is True
        assert _record(me["id"], step) is False
        activation.reset_memory()
        assert _record(me["id"], step) is False
        assert _count(step) == before + 1 and _rows(me["id"], step) == 1
    assert _record(me["id"], "first_login") is False                  # not a step: nothing written


def test_the_only_label_is_the_step():
    assert tuple(ACTIVATION._labelnames) == ("step",)  # noqa: SLF001
    assert ACTIVATION._name == "atp_activation"         # noqa: SLF001 - exposed as atp_activation_total


def _trade() -> Trade:
    return Trade(symbol="TCS", strategy_id="manual", direction=SignalDirection.LONG, entry_time=datetime.now(UTC),
                 entry_price=100.0, quantity=1, stop_loss=95.0)


def test_first_paper_trade_counted_once_and_live_not_at_all():
    from app.trading.persistence import persist_trade
    _, me = _who("g24-paper@example.com")
    _, live = _who("g24-live@example.com")
    before = _count("first_paper_trade")

    async def go(uid, mode):
        async with _session_factory() as session:
            rec = await persist_trade(session, await session.get(User, uid), _trade(), mode=mode)
            assert rec.id and rec.symbol == "TCS"                     # the trade path is untouched
    run(go(me["id"], "PAPER"))
    activation.reset_memory()
    run(go(me["id"], "PAPER"))
    run(go(live["id"], "LIVE"))
    assert _count("first_paper_trade") == before + 1
    assert _rows(me["id"], "first_paper_trade") == 1 and _rows(live["id"], "first_paper_trade") == 0


def test_opening_a_trade_journal_is_a_first_journal_review():
    headers, me = _who("g24-journal@example.com")
    _set_flag("discipline_engine", me["tenant_id"], on=True)

    async def seed():
        async with _session_factory() as session:
            t = TradeRecord(tenant_id=me["tenant_id"], user_id=me["id"], mode="PAPER", symbol="TCS", strategy_id="manual",
                            direction="LONG", entry_time=datetime.now(UTC), entry_price=1.0, quantity=1, stop_loss=0.5)
            session.add(t)
            await session.commit()
            return t.id
    trade_id = run(seed())
    before = _count("first_journal_review")
    assert client.get(f"/api/journal/trades/{trade_id}", headers=headers).status_code == 200
    assert client.get(f"/api/journal/trades/{trade_id}", headers=headers).status_code == 200
    assert _count("first_journal_review") == before + 1 and _rows(me["id"], "first_journal_review") == 1


def test_every_step_is_wired_where_it_happens():
    wired = {
        "app/auth/routes.py": ['"signup"'], "app/auth/google_routes.py": ['"signup"'],
        "app/brokers/routes.py": ['"broker_connect"'], "app/market_data/candles_routes.py": ['"first_chart"'],
        "app/trading/persistence.py": ['"first_paper_trade"'], "app/journal/routes.py": ['"first_journal_review"'],
    }
    for path, steps in wired.items():
        text = (BACKEND / path).read_text()
        for step in steps:
            assert f"record_step(session, user, {step}" in text, (path, step)
    assert (BACKEND / "app/auth/routes.py").read_text().count('record_step(session, user, "signup")') == 2   # form + invite


# --- the funnel math ----------------------------------------------------------------------------------------------------

NOW = datetime(2032, 5, 10, 12, 0, tzinfo=UTC)


def test_value_funnel_counts_medians_and_first_session_share():
    t0 = NOW - timedelta(days=10)
    people = [
        (t0, {"broker_connect": t0 + timedelta(hours=1), "first_chart": t0 + timedelta(hours=2),
              "first_paper_trade": t0 + timedelta(hours=2), "first_journal_review": t0 + timedelta(hours=30)}),
        (t0, {"broker_connect": t0 + timedelta(hours=3), "first_paper_trade": t0 + timedelta(hours=10)}),   # after the session
        (t0, {"broker_connect": t0 + timedelta(hours=5)}),                                                 # never traded
        (NOW - timedelta(hours=1), {}),                                                                    # still inside
        (NOW - timedelta(minutes=30), {"first_paper_trade": NOW - timedelta(minutes=10)}),                 # inside, done
    ]
    out = activation.value_funnel(people, now=NOW, first_session_hours=3)
    steps = {s["key"]: s for s in out["steps"]}
    assert [s["key"] for s in out["steps"]] == list(activation.STEP_KEYS)
    assert steps["signup"]["count"] == 5 and steps["broker_connect"]["count"] == 3
    assert steps["broker_connect"]["median_hours"] == 3.0                         # 1, 3, 5
    assert steps["first_paper_trade"]["count"] == 3 and steps["first_paper_trade"]["median_hours"] == 2.0    # 0.3, 2, 10
    assert steps["first_chart"]["median_hours"] == 2.0 and steps["first_journal_review"]["median_hours"] == 30.0
    fs = out["first_session"]
    assert fs == {"hours": 3, "decided": 4, "within": 2, "open": 1, "share_pct": 50.0}


def test_value_funnel_empty_and_even_median():
    out = activation.value_funnel([], now=NOW, first_session_hours=3)
    assert all(s["count"] == 0 and s["median_hours"] is None for s in out["steps"])
    assert out["first_session"]["share_pct"] is None and out["first_session"]["decided"] == 0
    assert activation.median([1.0, 4.0]) == 2.5 and activation.median([3.0, 1.0, 2.0]) == 2.0
    naive = datetime(2032, 5, 1, 9, 0)                                             # a naive time is read as UTC
    out = activation.value_funnel([(naive, {"first_paper_trade": naive + timedelta(hours=1)})], now=NOW, first_session_hours=3)
    assert out["first_session"]["within"] == 1


def test_first_session_is_a_setting():
    assert 1 <= ACTIVATION_FIRST_SESSION_HOURS <= 72
    assert "ACTIVATION_FIRST_SESSION_HOURS" in (BACKEND / "app/core/config.py").read_text()


# --- the admin read -----------------------------------------------------------------------------------------------------

def test_admin_funnel_is_admin_only_and_carries_the_steps():
    headers, owner = _who("g24-owner@example.com")
    _set_flag("ui_v2", owner["tenant_id"], on=True)
    _set_flag("growth_v2", owner["tenant_id"], on=True)
    assert client.get("/api/admin/console/activation", headers=headers).status_code == 403
    member = _member("g24-member@example.com", owner["tenant_id"], "USER")
    assert client.get("/api/admin/console/activation", headers=member).status_code == 403
    assert client.get("/api/admin/console/activation").status_code in (401, 403)

    admin, admin_me = _admin("g24-admin@example.com")
    _set_flag("growth_v2", admin_me["tenant_id"], on=True)
    _record(owner["id"], "broker_connect")
    body = client.get("/api/admin/console/activation?days=30", headers=admin).json()
    funnel = body["value_funnel"]
    steps = {s["key"]: s for s in funnel["steps"]}
    assert list(steps) == list(activation.STEP_KEYS)
    assert steps["signup"]["count"] >= 1 and steps["broker_connect"]["count"] >= 1
    assert steps["broker_connect"]["count"] <= steps["signup"]["count"]
    assert funnel["first_session"]["hours"] == ACTIVATION_FIRST_SESSION_HOURS
    assert set(funnel["first_session"]) == {"hours", "decided", "within", "open", "share_pct"}


def test_migration_chains_after_the_previous_head():
    text = (BACKEND / "alembic/versions/d0f0a1b2g204_aud_g24_activation_events.py").read_text()
    assert "revision: str = 'd0f0a1b2g204'" in text and "down_revision: Union[str, Sequence[str], None] = 'd0f0a1b2c3d8'" in text
    assert "uq_activation_events_user_step" in text

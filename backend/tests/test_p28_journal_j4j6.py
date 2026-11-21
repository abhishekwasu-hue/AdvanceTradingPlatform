"""P2-8 Journal v2 J4 + J5 + J6 (docs/plans/journal-v2-PLAN.md section 4): "My proof" facts (pure, facts only, no praise,
every line through the advice guard), his if-then plans and process goals (his own words; the first-20 rule on
progress), the Today tab, and the Telegram / e-mail delivery (default OFF; no feelings in a message; each sent once).
Every user, symbol and number here is made up."""
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.journal import deliver, plans, proof
from app.journal.entries import EntryError
from app.journal.review import advice_in
from app.journal.reviews import Unit
from tests.test_auth_api import client
from tests.test_m62_journal_layer import _h, _run, _trade, flag_on  # noqa: F401  (pytest fixture)
from tests.test_trading_api import _register_user_and_get_id

BACKEND = Path(__file__).resolve().parents[1]
PRAISE = re.compile(r"\b(great|well done|congrat\w*|amazing|proud|excellent|good job|keep it up)\b", re.I)


def _u(i, pnl, **kw):
    return Unit(id=i, kind="trade", symbol=f"DEMO{i}", setup="Buyer area", entry_time=f"2032-03-{10 + i:02d}T04:00:00+00:00",
                exit_time=f"2032-03-{10 + i:02d}T06:00:00+00:00" if pnl is not None else None, pnl=pnl, entry_order=i, **kw)


# --- J4: the facts ----------------------------------------------------------------------------------------------------

def test_facts_name_each_kind_of_good_process():
    units = [_u(1, -100.0, plan_followed="YES", exit_by_rule=True, from_signal=True),
             _u(2, 50.0, stop_set=False, from_signal=True, tags=("fomo_entry",)),
             _u(3, 80.0, exit_by_rule=True, tags=("early_exit",))]
    kinds = {(f.kind, f.unit_id) for f in proof.facts(units)}
    assert ("stop_kept", 1) in kinds and ("stop_kept", 3) in kinds and ("stop_kept", 2) not in kinds
    assert ("plan_on_loss", 1) in kinds and ("waited_setup", 1) in kinds and ("waited_setup", 2) not in kinds   # FOMO tag
    assert ("exit_as_planned", 1) in kinds and ("exit_as_planned", 3) not in kinds                           # early exit tag
    assert ("no_revenge", 2) in kinds                                                                        # after the loss on DEMO1
    assert proof.facts(units) == proof.facts(list(reversed(units)))                                          # same in, same out


def test_streak_and_recovered_drawdown():
    streak = [_u(i, 10.0, plan_followed="YES") for i in range(1, 6)] + [_u(6, 10.0, plan_followed="NO")]
    assert any(f.kind == "streak" and f.text.startswith("5 trades in a row") for f in proof.facts(streak))
    assert not any(f.kind == "streak" for f in proof.facts(streak[:4]))
    dip = [_u(1, 100.0), _u(2, -60.0), _u(3, -20.0), _u(4, 90.0)]
    (rec,) = [f for f in proof.facts(dip) if f.kind == "recovered"]
    assert rec.text == "Recovered a ₹80 dip over 3 trades without a rule-break tag." and rec.unit_id == 4
    assert not [f for f in proof.facts([_u(1, 100.0), _u(2, -60.0, tags=("revenge_trade",)), _u(3, 90.0)]) if f.kind == "recovered"]


def test_every_fact_is_a_plain_fact():
    units = [_u(i, (-1) ** i * 40.0, plan_followed="YES", exit_by_rule=True, from_signal=True) for i in range(1, 12)]
    found = proof.facts(units)
    assert found
    for f in found:
        assert advice_in(f.text) == [] and not PRAISE.search(f.text) and not re.search(r"\b(buy|sell|should|target)\b", f.text, re.I)
        assert f.at and f.unit_id is not None


def test_after_loss_line_and_last_good():
    units = [_u(1, 50.0, plan_followed="YES"), _u(2, 20.0, plan_followed="NO"), _u(3, -30.0, plan_followed="YES")]
    assert proof.after_loss_line(units) == "In your last 3 trades you followed your plan in 2."
    assert proof.after_loss_line(units[:2]) is None                      # the last trade did not lose
    assert proof.after_loss_line([_u(1, -10.0)]) is None                 # nothing answered yet
    assert proof.last_good(proof.facts(units)).at == "2032-03-13T06:00:00+00:00"
    assert proof.last_good([]) is None


# --- J5: goals and if-then plans --------------------------------------------------------------------------------------

def test_goal_progress_follows_the_first_20_rule():
    units = [_u(i, 10.0, plan_followed="YES" if i <= 3 else "NO") for i in range(1, 5)]
    small = proof.goal_progress("PLAN_FOLLOWED", 90, units, 20)
    assert (small["done"], small["base"], small["pct"], small["reached"], small["small"]) == (3, 4, None, False, True)
    big = proof.goal_progress("PLAN_FOLLOWED", 70, units, 4)
    assert big["pct"] == 75.0 and big["reached"] is True
    assert proof.goal_progress("CHECKED_IN", 50, [_u(1, 5.0), _u(2, 5.0, confidence=3)], 1)["done"] == 1


def test_clean_plan_keeps_his_words_and_refuses_the_rest():
    p = plans.clean_plan({"kind": "if_then", "if_text": " I feel  unease after entry ", "then_text": "check the stop and wait 5 minutes",
                          "trigger": "stop_hit"})
    assert p["if_text"] == "I feel unease after entry" and p["trigger"] == "STOP_HIT" and p["metric"] is None
    g = plans.clean_plan({"kind": "GOAL", "metric": "plan_followed", "target_pct": 90, "period": "month"})
    assert (g["metric"], g["target_pct"], g["period"]) == ("PLAN_FOLLOWED", 90, "MONTH")
    for bad in ({"kind": "IF_THEN", "if_text": "x"}, {"kind": "IF_THEN", "if_text": "x", "then_text": "y", "trigger": "PRICE"},
                {"kind": "GOAL", "metric": "PROFIT", "target_pct": 50, "period": "WEEK"}, {"kind": "GOAL", "metric": "STOP_KEPT", "target_pct": 0, "period": "WEEK"},
                {"kind": "GOAL", "metric": "STOP_KEPT", "target_pct": 80, "period": "DAY"}, {"kind": "WISH"}):
        with pytest.raises(EntryError):
            plans.clean_plan(bad)


def test_triggered_matches_a_stop_hit_or_a_loss():
    units = [_u(1, -40.0, exit_reason="Stop hit"), _u(2, -10.0, exit_reason="exit now (manual)"), _u(3, 30.0)]
    assert proof.triggered("STOP_HIT", units).id == 1 and proof.triggered("LOSS", units).id == 2
    assert proof.triggered("STOP_HIT", [_u(1, 30.0)]) is None


# --- routes -----------------------------------------------------------------------------------------------------------

def test_plans_proof_and_today_routes(flag_on):  # noqa: F811
    token, uid, tenant = _run(_register_user_and_get_id("p28-plans@example.com"))
    now = datetime.now(UTC)
    _trade(tenant, uid, symbol="P28STOP", entry_time=now - timedelta(hours=3), exit_time=now - timedelta(hours=1), exit_price=98.0,
           pnl=-20.0, exit_reason="Stop hit")
    r = client.post("/api/journal/plans", headers=_h(token), json={"kind": "IF_THEN", "if_text": "a trade stops me out",
                                                                   "then_text": "I take a short break before the next entry", "trigger": "STOP_HIT"})
    assert r.status_code == 201, r.text
    plan_id = r.json()["id"]
    client.post("/api/journal/plans", headers=_h(token), json={"kind": "GOAL", "metric": "STOP_KEPT", "target_pct": 90, "period": "WEEK"})
    listed = client.get("/api/journal/plans", headers=_h(token)).json()
    assert len(listed["plans"]) == 2 and listed["goals"][0]["metric"] == "STOP_KEPT" and listed["goals"][0]["pct"] is None   # 1 trade < 20
    today = client.get("/api/journal/today", headers=_h(token)).json()
    assert today["plans"][0]["then_text"] == "I take a short break before the next entry"
    assert today["reminders"][0]["because"] == "Stop hit on P28STOP"
    assert today["last_good"]["kind"] == "stop_kept"
    assert client.patch(f"/api/journal/plans/{plan_id}", headers=_h(token), json={"active": False}).json()["active"] is False
    assert client.get("/api/journal/today", headers=_h(token)).json()["reminders"] == []
    assert client.post("/api/journal/plans", headers=_h(token), json={"kind": "GOAL", "metric": "PROFIT"}).status_code == 422

    own = client.post("/api/journal/proof", headers=_h(token), json={"text": "Skipped a setup that was not mine", "happened_on": "2032-03-15"})
    assert own.status_code == 201
    page = client.get("/api/journal/proof", headers=_h(token)).json()
    keys = [i["key"] for i in page["items"]]
    assert own.json()["key"] in keys and any(k.startswith("stop_kept:") for k in keys) and page["after_loss"] is None
    auto = next(k for k in keys if k.startswith("stop_kept:"))
    assert client.put("/api/journal/proof/pin", headers=_h(token), json={"key": auto, "pinned": True}).status_code == 200
    assert client.get("/api/journal/proof", headers=_h(token)).json()["items"][0]["key"] == auto                       # pinned first
    other, _, _ = _run(_register_user_and_get_id("p28-plans-other@example.com"))
    assert client.patch(f"/api/journal/plans/{plan_id}", headers=_h(other), json={"active": True}).status_code == 404
    assert client.delete(f"/api/journal/proof/{own.json()['id']}", headers=_h(other)).status_code == 404
    assert client.delete(f"/api/journal/proof/{own.json()['id']}", headers=_h(token)).status_code == 200
    assert client.delete(f"/api/journal/plans/{plan_id}", headers=_h(token)).status_code == 200


def test_new_routes_are_behind_the_flag():
    token, _, _ = _run(_register_user_and_get_id("p28-plans-off@example.com"))
    for path in ("/api/journal/proof", "/api/journal/plans", "/api/journal/today"):
        assert client.get(path, headers=_h(token)).status_code == 503


# --- J6: delivery -----------------------------------------------------------------------------------------------------

def test_delivery_is_off_by_default_and_due_only_at_his_times():
    from app.me.prefs import defaults
    d = defaults("journal_delivery")
    assert not any(d[k] for k in ("telegram", "email", "daily", "weekly", "monthly", "checkin_prompts"))
    prefs = {**d, "telegram": True, "daily": True, "weekly": True, "monthly": True}
    friday_evening = datetime(2032, 3, 19, 12, 0, tzinfo=UTC)               # 17:30 IST, a Friday
    assert deliver.due(prefs, friday_evening, set()) == {"daily": "2032-03-19"}
    assert deliver.due(prefs, friday_evening - timedelta(hours=1), set()) == {}                     # 16:30 IST < 17:00
    assert deliver.due(prefs, friday_evening, {date(2032, 3, 19)}) == {}                             # a market holiday
    iso = date(2032, 3, 20).isocalendar()
    assert deliver.due(prefs, datetime(2032, 3, 20, 4, 0, tzinfo=UTC), set()) == {"weekly": f"{iso[0]}-W{iso[1]:02d}"}   # Saturday 09:30 IST
    assert deliver.due(prefs, datetime(2032, 4, 1, 4, 0, tzinfo=UTC), set())["monthly"] == "2032-03"


def test_messages_carry_no_feelings_and_go_once(flag_on, monkeypatch):  # noqa: F811
    review = {"lines": ["3 trades, 2 closed.", "Result: +₹80."], "gate": {"note": "Not enough trades yet (3 of 20)."},
              "done_well": "Stop was kept on all 2 trades.", "feelings": [{"feeling": "anxious", "count": 2}]}
    title, lines = deliver.message("daily", "2032-03-19", review)
    assert title == "Your day (2032-03-19)" and lines[0].startswith("Not enough") and lines[-1] == "Done well: Stop was kept on all 2 trades."
    assert "anxious" not in " ".join(lines)
    token, uid, tenant = _run(_register_user_and_get_id("p28-deliver@example.com"))
    _trade(tenant, uid, symbol="P28DLV", entry_time=datetime.now(UTC) - timedelta(minutes=10))
    sent: list[tuple[str, list[str]]] = []

    async def fake_send(session, user, prefs, title, lines, client):
        sent.append((title, lines))
        return True
    monkeypatch.setattr(deliver, "_send", fake_send)

    async def go(now):
        from app.db.models import User
        from tests.test_auth_api import _session_factory
        async with _session_factory() as session:
            user = await session.get(User, uid)
            prefs = {"telegram": True, "daily": True, "checkin_prompts": True, "daily_hour": 16, "weekly": False, "monthly": False}
            return await deliver.send_user(session, user, prefs, now, set())
    evening = datetime.now(UTC).replace(hour=12, minute=0)                    # 17:30 IST today
    first = _run(go(evening))
    again = _run(go(evening + timedelta(minutes=5)))
    titles = [t for t, _ in sent]
    assert again == 0 and titles.count("Journal") == 1                        # the check-in prompt, once
    assert first == len(sent) and all("check-in" in " ".join(lines) or "Your day" in t for t, lines in sent)


def test_journal_tour_id_is_allowed():
    from app.me import prefs
    assert "journal" in prefs.TOUR_IDS


def test_migration_and_privacy_lists():
    import inspect

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.me import account
    from app.retention import service
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    text = Path(script.get_revision("a5c7e9b1d3f6").path).read_text()
    assert "ROLLBACK_SAFE = True" in text and "'journal_proofs'" in text and "'journal_plans'" in text
    assert {"journal_proofs", "journal_plans"} <= {n for n, _ in account._DATASETS}
    src = inspect.getsource(service._erase_journal)
    assert "JournalProofRecord" in src and "JournalPlanRecord" in src

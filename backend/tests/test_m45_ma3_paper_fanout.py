"""M45 MA-3 (docs/design/M45_MULTI_ACCOUNT.md): multi-account orders on PAPER through simulated accounts.

- simulated accounts are the caller's own (never another member's), one Primary per user (moving it needs confirm),
  groups for Secondaries, sizing per account; behind `ui_v2` + `multi_account`;
- one ticket order becomes ONE child PAPER order per routed account through the ordinary ticket path, each with its own
  reference and its own trade tagged with the account; a result row per account; Primary only by default, the override
  picks groups / ALL; a switched-off account and an under-one-lot account are skipped with reasons;
- a refused Primary leaves the Secondaries unplaced (MB-5); each child exits on its own as any PAPER position (MB-4);
- nothing reaches a broker: every child is PAPER."""
import uuid

from sqlalchemy import select

from app.db.models import PaperSubAccountRecord, TradeRecord
from tests.test_auth_api import _session_factory, client
from tests.test_fno1_chain_ticket import _body, _run, setup  # noqa: F401 - the shared ticket fixture
from tests.test_z3b_paper_account import _member
from tests.test_z5_p1d_risk_by_default import _flag


def _accounts(t, *specs):
    _flag("multi_account", t["tenant_id"])
    ids = []
    for label, capital, patch in specs:
        made = client.post("/api/accounts/mine/paper", headers=t["headers"], json={"label": label, "capital": capital})
        assert made.status_code == 200, made.text
        ids.append(made.json()["id"])
        if patch:
            out = client.patch(f"/api/accounts/mine/paper/{ids[-1]}", headers=t["headers"], json=patch)
            assert out.status_code == 200, out.text
    return ids


def _order(t, body, run_on=None):
    out = client.post("/api/accounts/mine/paper/order", headers=t["headers"], json={"order": body, "run_on": run_on})
    assert out.status_code == 200, out.text
    return out.json()


def _trades(ids):
    async def go():
        async with _session_factory() as session:
            return {r.id: r for r in await session.scalars(select(TradeRecord).where(TradeRecord.id.in_(ids)))}
    return _run(go())


def test_off_by_default_and_own_accounts_only(setup):  # noqa: F811 - the fixture
    t, *_ = setup
    assert client.get("/api/accounts/mine/paper", headers=t["headers"]).headers.get("X-Feature-Disabled") == "multi_account"
    (pid,) = _accounts(t, ("Main", 500_000, {"role": "PRIMARY"}))
    other = _member(t)
    assert client.get("/api/accounts/mine/paper", headers=other["headers"]).json() == []
    assert client.patch(f"/api/accounts/mine/paper/{pid}", headers=other["headers"], json={"enabled": False}).status_code == 404
    dup = client.post("/api/accounts/mine/paper", headers=t["headers"], json={"label": "main", "capital": 1})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "duplicate"


def test_one_primary_with_confirmation_and_groups_for_secondaries(setup):  # noqa: F811 - the fixture
    t, *_ = setup
    a, b = _accounts(t, ("A", 100_000, {"role": "PRIMARY"}), ("B", 100_000, {"role": "SECONDARY", "group_name": "Family"}))
    refused = client.patch(f"/api/accounts/mine/paper/{b}", headers=t["headers"], json={"role": "PRIMARY"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "confirm_primary"
    moved = client.patch(f"/api/accounts/mine/paper/{b}", headers=t["headers"], json={"role": "PRIMARY", "confirm": True}).json()
    assert moved["role"] == "PRIMARY" and moved["group_name"] is None
    rows = {r["id"]: r for r in client.get("/api/accounts/mine/paper", headers=t["headers"]).json()}
    assert rows[a]["role"] == "SECONDARY"
    bad = client.patch(f"/api/accounts/mine/paper/{b}", headers=t["headers"], json={"group_name": "X"})
    assert bad.json()["detail"]["code"] == "not_secondary"
    bad = client.patch(f"/api/accounts/mine/paper/{a}", headers=t["headers"], json={"sizing_mode": "DOUBLE"})
    assert bad.json()["detail"]["code"] == "bad_sizing"


def test_one_order_becomes_one_paper_child_per_account_with_reasons_for_the_skipped(setup):  # noqa: F811 - the fixture
    t, expiry, strike, _ = setup
    p, fam, off, tiny = _accounts(
        t, ("Main", 500_000, {"role": "PRIMARY"}),
        ("Fam", 200_000, {"role": "SECONDARY", "group_name": "Family", "sizing_mode": "MULTIPLIER", "sizing_value": 2}),
        ("Off", 200_000, {"role": "SECONDARY", "group_name": "Family", "enabled": False}),
        ("Tiny", 50_000, {"role": "SECONDARY", "group_name": "Family", "sizing_mode": "MULTIPLIER", "sizing_value": 0.4}))
    body = _body(expiry, strike, lots=2, stop_loss=90.0, client_ref=uuid.uuid4().hex)

    # Default: the Primary only.
    only = _order(t, {**body, "client_ref": uuid.uuid4().hex})
    assert [(r["account_id"], r["status"], r["lots"]) for r in only["results"]] == [(p, "PLACED", 2)]

    out = _order(t, body, run_on="PRIMARY+Family")
    rows = {r["account_id"]: r for r in out["results"]}
    assert rows[p]["status"] == "PLACED" and rows[p]["lots"] == 2
    assert rows[fam]["status"] == "PLACED" and rows[fam]["lots"] == 4
    assert rows[off]["status"] == "SKIPPED" and "switched off" in rows[off]["reasons"][0]
    assert rows[tiny]["status"] == "SKIPPED" and rows[tiny]["reasons"] == ["under one lot after rounding down"]
    trades = _trades([rows[p]["trade_id"], rows[fam]["trade_id"]])
    assert {tr.paper_sub_account_id for tr in trades.values()} == {p, fam}
    assert all(tr.mode == "PAPER" for tr in trades.values())
    assert trades[rows[fam]["trade_id"]].quantity == 4 * 75

    # MB-4: each child is an ordinary PAPER position and exits on its own.
    assert client.post(f"/api/positions/{rows[fam]['trade_id']}/exit-now", headers=t["headers"]).status_code == 200
    assert _trades([rows[fam]["trade_id"]])[rows[fam]["trade_id"]].exit_time is not None


def test_a_refused_primary_leaves_the_secondaries_unplaced(setup):  # noqa: F811 - the fixture
    t, expiry, strike, _ = setup
    p, s = _accounts(t, ("Main", 500_000, {"role": "PRIMARY"}), ("Sec", 500_000, {"role": "SECONDARY", "group_name": "G"}))
    # A SELL without a stop is refused by the ticket's own checks.
    body = _body(expiry, strike, side="SELL", lots=1, client_ref=uuid.uuid4().hex)
    out = _order(t, body, run_on="ALL")
    rows = {r["account_id"]: r for r in out["results"]}
    assert rows[p]["status"] == "REFUSED"
    assert rows[s]["status"] == "SKIPPED" and rows[s]["reasons"] == ["the Primary account's order was not accepted"]

    async def count():
        async with _session_factory() as session:
            return len(list(await session.scalars(select(PaperSubAccountRecord).where(PaperSubAccountRecord.user_id == t["user_id"]))))
    assert _run(count()) == 2


def test_resting_orders_and_a_missing_reference_are_refused(setup):  # noqa: F811 - the fixture
    t, expiry, strike, _ = setup
    _accounts(t, ("Main", 500_000, {"role": "PRIMARY"}))
    out = client.post("/api/accounts/mine/paper/order", headers=t["headers"],
                      json={"order": _body(expiry, strike, stop_loss=90.0)})
    assert out.status_code == 409 and out.json()["detail"]["code"] == "client_ref_required"
    out = client.post("/api/accounts/mine/paper/order", headers=t["headers"],
                      json={"order": _body(expiry, strike, stop_loss=90.0, order_type="LIMIT", price=50.0, rest_if_waiting=True,
                                           client_ref=uuid.uuid4().hex)})
    assert out.status_code == 409 and out.json()["detail"]["code"] == "resting_not_supported"


def test_one_exit_closes_every_child_on_every_account_and_says_so_per_child(setup):  # noqa: F811 - the fixture
    """MA-4 / MB-4: the multi-account exit closes each account's own child through the ordinary exit path."""
    t, expiry, strike, _ = setup
    p, s = _accounts(t, ("Main", 500_000, {"role": "PRIMARY"}), ("Sec", 500_000, {"role": "SECONDARY", "group_name": "G"}))
    ref = uuid.uuid4().hex
    placed = _order(t, _body(expiry, strike, lots=1, stop_loss=90.0, client_ref=ref), run_on="ALL")
    ids = [r["trade_id"] for r in placed["results"]]
    assert all(r["status"] == "PLACED" for r in placed["results"]) and len(ids) == 2
    # One child already closed by hand: the multi-account exit closes only what is still open.
    assert client.post(f"/api/positions/{ids[0]}/exit-now", headers=t["headers"]).status_code == 200
    out = client.post("/api/accounts/mine/paper/exit", headers=t["headers"], json={"ref": ref})
    assert out.status_code == 200, out.text
    assert [(r["trade_id"], r["status"], r["account_id"]) for r in out.json()["results"]] == [(ids[1], "CLOSED", s)]
    assert all(tr.exit_time is not None for tr in _trades(ids).values())
    again = client.post("/api/accounts/mine/paper/exit", headers=t["headers"], json={"ref": ref})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "nothing_open"
    # Another member cannot close them by the reference.
    other = _member(t)
    assert client.post("/api/accounts/mine/paper/exit", headers=other["headers"], json={"ref": ref}).status_code in (404, 409)


def test_capital_check_alert_and_retry_of_one_account(setup):  # noqa: F811 - the fixture
    """MA-5: an account whose capital is below its lots' estimated margin is skipped with the figures (MB-6), the
    not-placed accounts raise one critical alert (MB-5), and ACCOUNT:<id> retries that one account."""
    from app.db.models import NotificationRecord
    t, expiry, strike, _ = setup
    p, small = _accounts(t, ("Main", 500_000, {"role": "PRIMARY"}), ("Small", 1_000, {"role": "SECONDARY", "group_name": "G"}))
    out = _order(t, _body(expiry, strike, lots=1, stop_loss=90.0, client_ref=uuid.uuid4().hex), run_on="ALL")
    rows = {r["account_id"]: r for r in out["results"]}
    assert rows[p]["status"] == "PLACED"
    assert rows[small]["status"] == "SKIPPED" and rows[small]["reasons"][0].startswith("capital 1,000 is below the estimated margin")

    async def alerts():
        async with _session_factory() as session:
            return list(await session.scalars(select(NotificationRecord).where(
                NotificationRecord.tenant_id == t["tenant_id"], NotificationRecord.title.like("Multi-account order:%"))))
    found = _run(alerts())
    assert len(found) == 1 and found[0].severity == "CRITICAL" and "Small" in found[0].message

    # The user raises the capital and retries that one account only.
    raised = client.patch(f"/api/accounts/mine/paper/{small}", headers=t["headers"], json={"capital": 500_000})
    assert raised.status_code == 200 and raised.json()["capital"] == 500_000
    retry = _order(t, _body(expiry, strike, lots=1, stop_loss=90.0, client_ref=uuid.uuid4().hex), run_on=f"ACCOUNT:{small}")
    assert [(r["account_id"], r["status"]) for r in retry["results"]] == [(small, "PLACED")]


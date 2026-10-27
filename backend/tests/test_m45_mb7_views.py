"""M45 MB-7 (docs/design/M45_MULTI_ACCOUNT.md): the positions list names the simulated PAPER account of each row.

- `account_label` on GET /api/positions is the caller's OWN paper_sub_accounts.label for a trade tagged with that account;
- null while `ui_v2` or `multi_account` is off (the answer otherwise exactly as before), and null for a trade with no account;
- another member's account label never leaks into the organisation-wide list (tenant AND user)."""
import uuid

from app.db.models import PaperSubAccountRecord
from tests.test_amw2_watchlists import _set_flag
from tests.test_auth_api import _session_factory, client
from tests.test_terminal_t1 import _member, _run, _trade, _who


def _account(who: dict, label: str) -> int:
    async def go():
        async with _session_factory() as session:
            row = PaperSubAccountRecord(tenant_id=who["tenant_id"], user_id=who["user_id"], label=label, capital=100_000.0)
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _labels(who: dict) -> dict:
    out = client.get("/api/positions", headers=who["headers"])
    assert out.status_code == 200, out.text
    return {p["id"]: p.get("account_label") for p in out.json()}


def test_account_label_only_with_both_flags_and_only_the_callers_own():
    owner = _who(f"mb7-{uuid.uuid4().hex[:6]}", flags=("ui_v2",))
    member = _member(owner)
    own = _trade(owner, symbol="OWNSYM", paper_sub_account_id=_account(owner, "Family"))
    plain = _trade(owner, symbol="PLAINSYM")
    other = _trade(member, symbol="TEAMSYM", paper_sub_account_id=_account(member, "Secret"))

    off = _labels(owner)                                           # `multi_account` off: no label anywhere
    assert {own, plain, other} <= set(off)
    assert off[own] is None and off[other] is None and off[plain] is None

    _set_flag("multi_account", owner["tenant_id"], on=True)
    on = _labels(owner)
    assert on[own] == "Family" and on[plain] is None
    assert on[other] is None                                       # a teammate's account never leaks
    assert "Secret" not in client.get("/api/positions", headers=owner["headers"]).text

    theirs = _labels(member)                                       # the member sees his own label, never the owner's
    assert theirs[other] == "Secret" and theirs[own] is None

    _set_flag("ui_v2", owner["tenant_id"], on=False)               # either flag off: exactly as before
    assert _labels(owner)[own] is None

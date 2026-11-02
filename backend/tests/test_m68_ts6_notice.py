"""M68 TS-6 (owner order M68): the one-time notice "ATP no longer pre-sets trading values — review your Trade settings"
(app/me/notices.py `no_presets_review`). Due once for an EXISTING user - one whose data holds a value from before
M68: a PAPER account an earlier release made (its capital stored, not marked as his own choice - the M68 PR-7 migration's
mark) or a value he saved in the older "My trading defaults" store. Never for a new user. Behind `ui_v2`. Dismissing it,
or saving his Trade settings, marks it seen in the prefs namespace `notices` (no migration). Made-up data only."""
from sqlalchemy import select

from app.db.models import PaperAccountRecord, User
from app.me import notices
from tests.test_auth_api import _session_factory, client
from tests.test_z5_p1d_risk_by_default import _flag, _run, _tenant_t

ID = "no_presets_review"
TEXT = "ATP no longer pre-sets trading values — review your Trade settings"
NOTICE = {"id": ID, "text": TEXT, "link": "/settings/trade-settings", "label": "Open Trade settings"}


def _org(tag: str, ui_v2: bool = True) -> dict:
    t = _tenant_t(f"ts6-{tag}")
    _flag("ui_v2", t["tenant_id"], ui_v2)
    return t


def _account(t: dict, capital: float | None, saved: bool) -> None:
    """A PAPER account row as a release left it (`saved`: the capital_saved mark)."""
    async def go():
        async with _session_factory() as session:
            user = await session.get(User, t["user_id"])
            row = await session.scalar(select(PaperAccountRecord).where(PaperAccountRecord.user_id == user.id))
            if row is None:
                row = PaperAccountRecord(tenant_id=user.tenant_id, user_id=user.id, paused=False)
                session.add(row)
            row.starting_capital, row.capital_saved = capital, saved
            await session.commit()
    _run(go())


def _mine(t: dict) -> list:
    got = client.get("/api/me/notices", headers=t["headers"])
    assert got.status_code == 200
    return [n for n in got.json()["notices"] if n["id"] == ID]


def test_an_existing_paper_account_gets_the_notice_once_until_dismissed():
    t = _org("old")
    _account(t, 1_000_000.0, saved=False)                      # made by an earlier release with ATP's start capital
    assert _mine(t) == [NOTICE]
    assert notices.TEXT[notices.NO_PRESETS_REVIEW] == TEXT
    assert _mine(t) == _mine(t)                                  # reading does not mark it
    out = client.post(f"/api/me/notices/{ID}/seen", headers=t["headers"])
    assert out.status_code == 200 and [n for n in out.json()["notices"] if n["id"] == ID] == []
    assert _mine(t) == []
    prefs = client.get("/api/me/prefs", headers=t["headers"]).json()
    assert prefs["prefs"]["notices"]["no_presets_review_seen_at"]
    version = prefs["versions"]["notices"]
    client.post(f"/api/me/notices/{ID}/seen", headers=t["headers"])    # a second dismissal keeps the first mark
    assert client.get("/api/me/prefs", headers=t["headers"]).json()["versions"]["notices"] == version


def test_a_value_saved_in_my_trading_defaults_makes_him_an_existing_user():
    t = _org("older-store")
    assert _mine(t) == []
    assert client.put("/api/me/prefs/trading_defaults", headers=t["headers"], json={"max_lots": 4}).status_code == 200
    assert _mine(t) == [NOTICE]


def test_never_for_a_new_user():
    new = _org("new")
    assert _mine(new) == []                                       # no account, nothing saved
    _account(new, None, saved=False)                              # a new account: capital Not set
    assert _mine(new) == []
    chose = _org("chose")
    _account(chose, 250_000.0, saved=True)                        # he chose his own capital (M68 PR-7)
    assert _mine(chose) == []


def test_saving_his_trade_settings_marks_it_seen():
    t = _org("save")
    _account(t, 1_000_000.0, saved=False)
    assert len(_mine(t)) == 1
    out = client.put("/api/me/trade-settings", headers=t["headers"], json={"values": {"position_size.lots": 2}})
    assert out.status_code == 200, out.text
    assert _mine(t) == []
    assert client.get("/api/me/prefs", headers=t["headers"]).json()["prefs"]["notices"]["no_presets_review_seen_at"]


def test_a_refused_save_does_not_mark_it():
    t = _org("refused")
    _account(t, 1_000_000.0, saved=False)
    assert client.put("/api/me/trade-settings", headers=t["headers"], json={"values": {"no.such": 1}}).status_code == 422
    assert len(_mine(t)) == 1


def test_behind_ui_v2_and_per_user():
    off = _org("v2off", ui_v2=False)
    _account(off, 1_000_000.0, saved=False)
    assert _mine(off) == []
    a, b = _org("a"), _org("b")
    _account(a, 1_000_000.0, saved=False)
    _account(b, 1_000_000.0, saved=False)
    client.post(f"/api/me/notices/{ID}/seen", headers=a["headers"])
    assert _mine(a) == [] and len(_mine(b)) == 1


def test_the_other_notices_keep_their_shape():
    # the two risk-limit notices carry no label: the shell keeps its "Open Risk limits" for them
    assert set(notices.LABEL) == {notices.NO_PRESETS_REVIEW}

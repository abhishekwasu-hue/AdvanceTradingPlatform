"""Analyze a chart PR-A4 (design PR-D, CA-5): the organisation-only share link and its structure-only copy.

Made-up data only: the synthetic chart and the fake engine's made-up answer (tests/chart_analyze_fakes.py); no network."""
import json
import re
from datetime import datetime, timedelta, timezone

from app.chart_analyze import SHARE_FLAG, service, share
from app.chart_analyze.engine import Answer
import pytest
from fastapi import HTTPException

from app.db.models import ChartAnalysisRecord, User
from app.platform import controls
from app.platform.flag_catalog import FLAG_INFO
from tests.chart_analyze_fakes import answer
from tests.test_auth_api import _session_factory, client
from tests.test_chart_analyze_api import BASE, _fake_answer, _isolated, _owner, _row, _run, _set_settings, _upload  # noqa: F401 - _isolated is autouse
from tests.test_z3b_paper_account import _member


def _share_flag(tenant_id: int) -> None:
    async def go():
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            entry = flags.get(SHARE_FLAG) or {"on": False, "tenants": []}
            flags[SHARE_FLAG] = {"on": False, "tenants": sorted(set(entry.get("tenants") or []) | {tenant_id})}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


def _sowner(tag: str) -> dict:
    who = _owner(tag)
    _share_flag(who["tenant_id"])
    return who


def _analysed(who: dict, monkeypatch, reply: dict | None = None) -> int:
    if reply is None:
        _fake_answer(monkeypatch, "forming")
    else:
        async def fake_ask(session, user, tenant, instr, png, settings, *, now, client=None):
            return Answer(text=json.dumps(reply), error=None, provider="anthropic", model="test", cost_usd=0.01, billed_to_user_key=False)
        monkeypatch.setattr(service, "ask", fake_ask)
    item = _upload(who).json()
    assert client.post(f"{BASE}/items/{item['id']}/analyze", headers=who["headers"]).json()["status"] == "done"
    return item["id"]


def _share(who: dict, item_id: int):
    return client.post(f"{BASE}/items/{item_id}/share", headers=who["headers"])


def _open(who: dict, token: str):
    return client.get(f"{BASE}/shared/{token}", headers=who["headers"])


def _texts(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):          # the drawing's internal ids (never shown) are not texts
        return [t for k, v in value.items() if k != "id" for t in _texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    return []


def _prices(value, key: str = "") -> list:
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _prices(v, k)]
    if isinstance(value, list):
        return [p for v in value for p in _prices(v, key)]
    return [value] if key in ("price", "price_low", "price_high", "low", "high") and value is not None else []


def _named_reply() -> dict:
    """A made-up answer whose texts name the chart, prices and a date (all must stay out of a shared copy)."""
    reply = answer("forming")
    reply["story"] = ["DEMO rose from the made-up lows near 98.", "The pullback reached a buyer area; a handover is forming."]
    reply["trend"]["text"] = "higher highs and higher lows on demo"
    reply["scenarios"]["primary"] = "Made-up primary scenario."
    reply["scenarios"]["alternative"] = "The NSE session on 14 Oct broke the area."
    reply["key_levels"][0]["name"] = "Made-up buyer area"
    reply["annotations"]["lines"][0]["name"] = "line from 3 touches"
    return reply


# --- the flag ------------------------------------------------------------------------------------------------------------

def test_share_flag_is_registered_off_and_depends_on_chart_analyze():
    assert SHARE_FLAG in controls.FEATURE_FLAGS and SHARE_FLAG in controls.DEFAULT_OFF_FLAGS
    assert FLAG_INFO[SHARE_FLAG].depends_on == ("chart_analyze",)


def test_share_flag_off_answers_503_on_share_and_open_but_stopping_still_works(monkeypatch):
    who = _owner("share-off")
    item_id = _analysed(who, monkeypatch)
    for method, path in (("POST", f"/items/{item_id}/share"), ("GET", "/shared/abc.def")):
        response = client.request(method, BASE + path, headers=who["headers"])
        assert response.status_code == 503 and response.headers.get("X-Feature-Disabled") == SHARE_FLAG, (method, path)
    assert client.delete(f"{BASE}/items/{item_id}/share", headers=who["headers"]).status_code == 200


def test_chart_analyze_off_answers_503_on_every_share_route():
    who = _owner("share-ca-off", flag=False)
    _share_flag(who["tenant_id"])
    for method, path in (("POST", "/items/1/share"), ("DELETE", "/items/1/share"), ("GET", "/shared/abc.def")):
        response = client.request(method, BASE + path, headers=who["headers"])
        assert response.status_code == 503 and response.headers.get("X-Feature-Disabled") == "chart_analyze", (method, path)


def test_anonymous_cannot_open_a_link(monkeypatch):
    who = _sowner("share-anon")
    token = _share(who, _analysed(who, monkeypatch)).json()["share"]["token"]
    assert client.get(f"{BASE}/shared/{token}").status_code == 401


# --- making, opening and stopping a link -----------------------------------------------------------------------------------

def test_the_owner_makes_a_link_a_member_of_the_organisation_opens_it(monkeypatch):
    who = _sowner("share-ok")
    item_id = _analysed(who, monkeypatch)
    body = _share(who, item_id)
    assert body.status_code == 200, body.text
    link = body.json()["share"]
    expires = datetime.fromisoformat(link["expires_at"])
    assert timedelta(days=6, hours=23) < expires - datetime.now(timezone.utc) <= timedelta(days=7)      # share_expiry_days
    assert "_share" not in json.dumps(body.json()["result"])                                            # the state is never in the result
    # the owner's own views carry the live link
    assert client.get(f"{BASE}/items/{item_id}", headers=who["headers"]).json()["share"]["token"] == link["token"]
    [listed] = [h for h in client.get(f"{BASE}/items", headers=who["headers"]).json()["items"] if h["id"] == item_id]
    assert listed["share"]["token"] == link["token"] and listed["summary"]["symbol"] == "DEMO"
    member = _member(who)
    opened = _open(member, link["token"])
    assert opened.status_code == 200, opened.text
    data = opened.json()
    assert data["is_yours"] is False and data["result"]["shared"] is True and data["result"]["shared_note"] == share.SHARED_NOTE
    assert data["result"]["setup"]["status"] == "forming" and data["disclaimer"]
    assert _open(who, link["token"]).json()["is_yours"] is True


def test_the_shared_copy_has_no_name_no_price_no_date_and_no_number(monkeypatch):
    who = _sowner("share-redact")
    item_id = _analysed(who, monkeypatch, _named_reply())
    token = _share(who, item_id).json()["share"]["token"]
    data = _open(_member(who), token).json()
    result = data["result"]
    assert result["recognised"] == {"symbol": None, "exchange": None, "timeframe": None, "first_bar": None, "last_bar": None}
    assert _prices(result) == [] and result["what_to_wait_for"]["alert"] is None
    assert all(h["timeframe"] is None for h in result["htf_context"])
    texts = [t for t in _texts(result) if t != result["disclaimer"]]
    for t in texts:
        assert not re.search(r"\d", t), t
        assert not re.search(r"(?i)\b(demo|nse)\b", t), t
    assert result["story"] == [share.NOT_SHARED, "The pullback reached a buyer area; a handover is forming."]   # structure words stay
    assert result["trend"]["text"] == share.NOT_SHARED and result["scenarios"]["alternative"] == share.NOT_SHARED
    assert result["key_levels"][0]["name"] == "Made-up buyer area"
    assert result["annotations"]["lines"][0]["name"] == share.NOT_SHARED
    # the drawing stays (pixel coordinates draw the structure over a plain background), its prices do not
    assert result["annotations"]["areas"][0]["x0"] == 100 and result["annotations"]["areas"][0]["price_low"] is None
    # nothing points at the picture or the item
    assert set(data) == {"result", "mode", "expires_at", "is_yours", "disclaimer"} and "url" not in json.dumps(result)


def test_another_organisation_and_a_forged_link_get_the_same_404(monkeypatch):
    who = _sowner("share-tenancy")
    token = _share(who, _analysed(who, monkeypatch)).json()["share"]["token"]
    other = _sowner("share-other")
    refused = _open(other, token)
    assert refused.status_code == 404 and refused.json()["detail"]["code"] == "link_gone"
    payload, mac = token.split(".")
    for bad in (f"{payload}.{'0' * len(mac)}", "nonsense", f"{payload}x.{mac}", "a.b"):
        assert _open(who, bad).status_code == 404, bad
    assert _open(who, "x" * 300).status_code == 422                     # longer than any link this server makes


def test_only_the_owner_shares_and_stops_and_only_a_finished_analysis(monkeypatch):
    who = _sowner("share-own")
    item_id = _analysed(who, monkeypatch)
    member = _member(who)
    assert _share(member, item_id).status_code == 404
    assert client.delete(f"{BASE}/items/{item_id}/share", headers=member["headers"]).status_code == 404
    fresh = _upload(who).json()
    refused = _share(who, fresh["id"])
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "not_analysed"


def test_stopping_and_a_new_link_end_the_older_link(monkeypatch):
    who = _sowner("share-stop")
    member = _member(who)
    item_id = _analysed(who, monkeypatch)
    first = _share(who, item_id).json()["share"]["token"]
    second = _share(who, item_id).json()["share"]["token"]
    assert first != second and _open(member, first).status_code == 404 and _open(member, second).status_code == 200
    stopped = client.delete(f"{BASE}/items/{item_id}/share", headers=who["headers"])
    assert stopped.status_code == 200 and stopped.json()["share"] is None and stopped.json()["result"]["setup"]["status"] == "forming"
    assert _open(member, second).status_code == 404
    third = _share(who, item_id).json()["share"]["token"]
    assert _open(member, third).status_code == 200


def test_a_link_expires(monkeypatch):
    who = _sowner("share-expire")
    _set_settings(share_expiry_days=1)
    try:
        item_id = _analysed(who, monkeypatch)
        token = _share(who, item_id).json()["share"]["token"]
        later = datetime.now(timezone.utc) + timedelta(days=1, minutes=1)

        async def go():
            async with _session_factory() as session:
                viewer = await session.get(User, who["user_id"])
                return await service.open_shared(session, viewer, token, now=later)
        with pytest.raises(HTTPException) as exc:
            _run(go())
        assert exc.value.status_code == 410 and exc.value.detail["code"] == "link_expired"
        # the owner no longer sees a live link once it expired
        row = _row(item_id)
        assert service.as_dict(row, now=later)["share"] is None
    finally:
        _set_settings(share_expiry_days=7)


def test_deleting_the_analysis_ends_its_link_and_a_late_share_never_brings_the_result_back(monkeypatch):
    who = _sowner("share-delete")
    member = _member(who)
    item_id = _analysed(who, monkeypatch)
    token = _share(who, item_id).json()["share"]["token"]
    assert client.delete(f"{BASE}/items/{item_id}", headers=who["headers"]).status_code == 200
    assert _open(member, token).status_code == 404
    row = _row(item_id)
    assert row.status == "deleted" and row.result_json is None

    async def late():
        async with _session_factory() as session:
            stale = ChartAnalysisRecord(id=item_id, tenant_id=who["tenant_id"], user_id=who["user_id"], status="done",
                                        result_json=json.dumps(answer("formed")))
            await service._set_share_state(session, stale, share.new_state({}, datetime.now(timezone.utc), 7))
    with pytest.raises(HTTPException) as exc:
        _run(late())
    assert exc.value.status_code == 409
    assert _row(item_id).result_json is None


def test_strikes_and_the_result_never_see_the_link_state(monkeypatch):
    who = _sowner("share-state")
    item_id = _analysed(who, monkeypatch)
    _share(who, item_id)
    stored = json.loads(_row(item_id).result_json)
    assert stored[share.STATE_KEY]["active"] is True and stored[share.STATE_KEY]["version"] == 1
    item = client.get(f"{BASE}/items/{item_id}", headers=who["headers"]).json()
    assert share.STATE_KEY not in item["result"]


def test_share_setting_bounds():
    from app.chart_analyze import settings as settings_mod
    assert settings_mod.DEFAULTS["share_expiry_days"] == 7
    for bad in (0, 31):
        with pytest.raises(settings_mod.SettingsError):
            settings_mod.validate_update(settings_mod.DEFAULTS, {"share_expiry_days": bad})


# --- the redaction itself ---------------------------------------------------------------------------------------------------

def test_clean_text_keeps_structure_words_and_leaves_out_numbers_and_names():
    names = ("DEMO", "NSE")
    assert share.clean_text("The pullback reached a seller area; a handover is forming.", names).startswith("The pullback")
    for bad in ("near 23,000", "on 14 Oct", "demo made a higher high", "the NSE close", "a 15m change"):
        assert share.clean_text(bad, names) == share.NOT_SHARED, bad
    assert share.clean_text("a demolition of the low", names).startswith("a demolition")       # whole words only


def test_a_token_names_its_item_owner_organisation_version_and_expiry():
    until = datetime(2030, 1, 2, tzinfo=timezone.utc)
    token = share.make_token(5, 6, 7, 3, until)
    assert share.read_token(token) == (5, 6, 7, 3, until)
    assert share.read_token(token[:-1] + ("0" if token[-1] != "0" else "1")) is None

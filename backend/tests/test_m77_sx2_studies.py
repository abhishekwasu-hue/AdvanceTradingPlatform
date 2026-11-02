"""M77 SX-2: the saved study - snapshot builder, `copilot_studies`, the routes and the account ZIP dataset.

- the snapshot is the server's own reads: a number the browser sends is never read (unknown fields are dropped);
- the same inputs give the same snapshot bytes; the same stored study downloads as the same JSON bytes;
- no look-ahead: a bar whose END is after the study time is never read;
- every section on / off; a section without a source says "not available" with a reason, never blank;
- user B's data never reaches A's snapshot / JSON (two-user fixture, string scan incl. emails and notes); no account
  identifier; another member's study reads as "not found";
- size bounds refuse with a plain message; plain structure words only (no advice wording); every page's footer line.
"""
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from app.charts import price_action_layer as pal
from app.core import config, rate_limit
from app.platform import controls, flag_catalog
from app.signals.language import is_clean
from app.study import routes as SR
from app.study import snapshot as S
from app.study import store
from tests.ch3_pa_cases import frame as fixture_frame
from tests.test_auth_api import client  # noqa: F401 - the app client
from tests.test_phase_l_ai import _owner
from tests.test_settings_a_connect_ai import _flags

F = fixture_frame()
CUT = pd.Timestamp(F["bar_end"].iloc[400]).to_pydatetime().replace(microsecond=0)
CTX = {"ok": True, "trend": "UP", "sides": set(), "band": None, "phase": "impulse", "counter": "pullback", "asof": "2026-01-02"}
ADVICE = ("buy", "sell", "target", "stop-loss", "stop loss", "entry", "recommend", "profit")


def _zone_rows(price: float, tf: str = "15m"):
    mk = lambda key, lo, hi, state: {"zone_key": key, "zone_type": "ORDER_BLOCK", "timeframe": tf, "zone_low": lo, "zone_high": hi,
                                     "state": state, "touches": 1, "formed_at": CUT - timedelta(days=3),
                                     "last_event": {"event": "TOUCH", "time": CUT - timedelta(days=1)}}
    return [mk("zk-secret-1", round(price * 0.990, 2), round(price * 0.994, 2), "fresh"),
            mk("zk-secret-2", round(price * 1.004, 2), round(price * 1.008, 2), "tested"),
            mk("zk-secret-3", round(price * 1.012, 2), round(price * 1.016, 2), "flipped")]


def _inputs(**over):
    cut = F[F["bar_end"] <= CUT]
    price = float(cut["close"].iloc[-1])
    base = dict(created_at=CUT, key="NSE:NIFTY BANK", instrument_kind="INDEX", timeframes=["15m", "1h"], sections=list(S.SECTIONS),
                frames={"15m": (F, "lake:15m"), "1h": (F, "own:60m")},
                zones=S.ZoneRead(rows=_zone_rows(price), as_of=CUT - timedelta(hours=10)), daily_context=CTX,
                quote=None, drawings=[{"kind": "hline", "timeframe": "15min", "drawing": {"anchors": [{"p": price}], "style": {"color": "#2962ff"}}}],
                title="My look", notes="My own words: watch the area", checklist_rules=["Trend is clear", "Area is fresh"],
                checklist_answers=[True], app_version="9.9.9")
    base.update(over)
    return S.StudyInputs(**base)


def _texts(obj, key=""):
    if isinstance(obj, str):
        yield key, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _texts(v, k)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _texts(v, key)


# ---------------------------------------------------------------------------------------------------------------------
# Pure part
# ---------------------------------------------------------------------------------------------------------------------
def test_same_inputs_same_bytes_and_fingerprint():
    a, b = S.canonical(S.assemble(_inputs())), S.canonical(S.assemble(_inputs()))
    assert a == b and S.fingerprint(a) == S.fingerprint(b) and len(S.fingerprint(a)) == 64
    snap = json.loads(a)
    assert snap["schema"] == S.SCHEMA and snap["timeframes"] == ["1h", "15m"]          # largest first
    assert snap["app_version"] == "9.9.9" and snap["reading_source"] == "ATP structure readers"


def test_no_lookahead_later_and_forming_bars_never_change_the_snapshot():
    cut = F[F["bar_end"] <= CUT]
    forming = cut.iloc[[-1]].assign(timestamp=pd.Timestamp(CUT) - pd.Timedelta(minutes=5), bar_end=pd.Timestamp(CUT) + pd.Timedelta(minutes=10),
                                    close=cut["close"].iloc[-1] * 3)
    with_later = S.assemble(_inputs(frames={"15m": (pd.concat([F, forming]), "lake:15m"), "1h": (F, "own:60m")}))
    only_closed = S.assemble(_inputs(frames={"15m": (cut, "lake:15m"), "1h": (cut, "own:60m")}))
    assert S.canonical(with_later) == S.canonical(only_closed)
    chart = only_closed["data"]["charts"][1]
    assert chart["as_of"] == pd.Timestamp(CUT).isoformat() and chart["bars"][-1][0] == pal._epoch(cut["timestamp"].iloc[-1])


@pytest.mark.parametrize("sections", [[s] for s in S.SECTIONS] + [list(S.SECTIONS), ["cover", "notes"]])
def test_every_section_on_and_off(sections):
    snap = S.assemble(_inputs(sections=sections))
    assert snap["sections"] == [s for s in S.SECTIONS if s in sections]
    assert snap["footer"]["text"] == S.INFO_LINE and snap["footer"]["sources"]           # the footer is never a choice
    for s in S.SECTIONS:
        if s in S.NOT_BUILT:
            assert (s in snap["not_available"]) == (s in sections) and (not snap["not_available"].get(s) or snap["not_available"][s].strip())
        elif s in ("cover", "charts", "structure", "notes"):
            assert (s in snap["data"]) == (s in sections)
    assert ("areas" in snap["data"]) == bool({"charts", "structure", "areas"} & set(sections))
    assert ("drawings" in snap["data"]) == ("charts" in sections)


def test_missing_sources_say_not_available_never_blank_or_zero():
    empty = pd.DataFrame(columns=pal.FRAME_COLS)
    snap = S.assemble(_inputs(frames={"15m": (empty, None), "1h": (empty, None)}, zones=S.ZoneRead(reason=S.AREAS_OFF), daily_context=None))
    d = snap["data"]
    assert all(not c["available"] and c["reason"] for c in d["charts"]) and all(not r["available"] and r["reason"] for r in d["structure"]["timeframes"])
    assert d["cover"]["live_price"] == {"value": None, "as_of": None, "reason": S.NO_QUOTE}
    assert d["areas"]["rows"] == [] and d["areas"]["reason"] == S.AREAS_OFF
    assert not d["structure"]["daily_trend"]["available"]
    assert all(s["words"] for s in snap["footer"]["sources"])


def test_areas_labelled_per_study_never_by_zone_key_and_timeframes_never_merged():
    cut = F[F["bar_end"] <= CUT]
    price = float(cut["close"].iloc[-1])
    rows = _zone_rows(price, "15m")[:2] + [dict(_zone_rows(price, "1h")[2], zone_key="zk-secret-4")]
    snap = S.assemble(_inputs(zones=S.ZoneRead(rows=rows, as_of=CUT - timedelta(hours=10))))
    assert "zk-secret" not in S.canonical(snap).decode()
    areas = snap["data"]["areas"]["rows"]
    assert sorted(a["label"] for a in areas) == ["B1", "S1", "S2"]
    for a in areas:
        assert a["formed_at"] and a["last_visit"] and a["distance_points"] >= 0 and a["tf_words"]
    by_tf = {r["tf"]: r for r in snap["data"]["structure"]["timeframes"]}
    near15 = by_tf["15m"]["nearest"]
    assert near15["above"]["label"] == "S1" and near15["below"]["label"] == "B1"
    assert by_tf["1h"]["nearest"]["below"] is None and by_tf["1h"]["nearest"]["above"]["label"] == "S2"   # only its own timeframe


def test_reference_price_is_the_live_price_else_the_labelled_last_close():
    cut = F[F["bar_end"] <= CUT]
    snap = S.assemble(_inputs())
    ref = snap["data"]["cover"]["reference_price"]
    assert ref["value"] == pal._num(float(cut["close"].iloc[-1])) and ref["source"] == "last close (15-minute)"
    live = S.assemble(_inputs(quote={"value": 51234.5, "as_of": "2026-01-02T09:30:00+00:00"}))["data"]["cover"]
    assert live["live_price"]["value"] == 51234.5 and live["reference_price"]["source"] == "live price"


def test_notes_verbatim_and_checklist_answers():
    notes = "Line one\n\u0915\u093e\u0932 \u0915\u093e area  (two spaces)"
    d = S.assemble(_inputs(notes=notes))["data"]["notes"]
    assert d["text"] == notes
    assert d["checklist"] == [{"rule": "Trend is clear", "answer": "yes"}, {"rule": "Area is fresh", "answer": "not answered"}]


def test_plain_structure_words_only_no_advice_wording():
    snap = S.assemble(_inputs(notes=None, title=None, checklist_rules=[]))
    texts = [t for k, t in _texts(snap)]
    assert texts and all(is_clean(t) for t in texts)
    assert not [t for t in texts if any(w in t.lower() for w in ADVICE)]


def test_size_bound_refuses_with_a_plain_message():
    with pytest.raises(S.StudyTooLarge) as exc:
        S.check_size(b"x" * 3000, limit=2048)
    assert str(exc.value).startswith("This study is too large to save (3 KB; the limit is 2 KB).")


# ---------------------------------------------------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def served(monkeypatch):
    now = datetime.now(UTC).replace(microsecond=0)
    shift = pd.Timestamp(now) - pd.Timestamp(F["bar_end"].iloc[-1]) - pd.Timedelta(minutes=30)
    recent = F.assign(timestamp=F["timestamp"] + shift, bar_end=F["bar_end"] + shift)
    price = float(recent["close"].iloc[-1])
    seen = {"loads": []}

    async def load(session, key, tf, start, end, max_bars=None, private=None):
        seen["loads"].append((key, tf, private))
        return recent, "lake:15m"

    async def zones(session, key, when, tenant_id, user_id):
        from app.price_action.zones.read import Snapshot
        seen["zones"] = (key, tenant_id, user_id)
        rows = [dict(r, formed_at=when - timedelta(days=3), last_event={"event": "TOUCH", "time": when - timedelta(days=1)})
                for r in _zone_rows(price)]
        return Snapshot(1, when - timedelta(hours=2), "tenant:1", price, rows)

    async def ctx(session, key, today, *a, **k):
        return dict(CTX)

    async def quote(session, user, exchange, symbol):
        return None

    from app.charts import zones_layer as zl
    from app.screener.then import trend
    monkeypatch.setattr(pal, "load_frame", load)
    monkeypatch.setattr(zl, "snapshot_for", zones)
    monkeypatch.setattr(trend, "daily_context", ctx)
    monkeypatch.setattr(S, "live_quote", quote)
    rate_limit.reset("study_save")
    _flags(ui_v2=True, study_export=True, market_zones=True)
    yield {"recent": recent, "price": price, "seen": seen}
    _flags(ui_v2=False, study_export=False, market_zones=False)


def _member(tag):
    email = f"m77-{tag}-{uuid.uuid4().hex[:8]}@example.com"
    headers, me = _owner(email)
    return headers, me, email


BODY = {"instrument": "NSE:NIFTY BANK", "timeframes": ["15m", "1h"], "title": "Bank look", "checklist": [True]}


def test_flag_registered_off_by_default_with_a_fate():
    assert SR.FLAG in controls.FEATURE_FLAGS and SR.FLAG in controls.DEFAULT_OFF_FLAGS
    assert flag_catalog.FLAG_INFO[SR.FLAG].depends_on == ("ui_v2",) and SR.FLAG in flag_catalog.FINISH


def test_routes_404_while_off(served):
    headers, _, _ = _member("off")
    _flags(ui_v2=True, study_export=False)
    assert client.get("/api/studies", headers=headers).status_code == 404
    assert client.post("/api/studies", json=BODY, headers=headers).status_code == 404
    _flags(ui_v2=False, study_export=True)
    assert client.get("/api/studies", headers=headers).status_code == 404


def test_the_server_ignores_numbers_the_browser_sends(served):
    headers, _, _ = _member("nums")
    body = {**BODY, "ltp": 99999.25, "price": 99999.25, "areas": [{"low": 88888.75, "high": 88889.75}],
            "snapshot": {"data": {"cover": {"live_price": {"value": 77777.5}}}}}
    r = client.post("/api/studies", json=body, headers=headers)
    assert r.status_code == 201, r.text
    got = client.get(f"/api/studies/{r.json()['id']}", headers=headers).json()
    text = json.dumps(got)
    assert "99999.25" not in text and "88888.75" not in text and "77777.5" not in text
    assert got["snapshot"]["data"]["cover"]["reference_price"]["value"] == pal._num(served["price"])
    assert [tf for _, tf, _ in served["seen"]["loads"]] == ["1h", "15m"]


def test_two_users_never_see_each_other_and_no_identifier_in_the_snapshot(served):
    a_headers, a_me, a_email = _member("a")
    b_headers, b_me, b_email = _member("b")
    a = client.post("/api/studies", json={**BODY, "notes": "alpha private note"}, headers=a_headers)
    b = client.post("/api/studies", json={**BODY, "notes": "bravo private note"}, headers=b_headers)
    assert a.status_code == 201 and b.status_code == 201
    a_id, b_id = a.json()["id"], b.json()["id"]
    assert served["seen"]["zones"][1:] == (b_me["tenant_id"], b_me["id"])          # the reader's own scope
    a_list = client.get("/api/studies", headers=a_headers).json()
    assert [i["id"] for i in a_list["items"]] == [a_id] and a_list["total"] == 1
    for path in (f"/api/studies/{b_id}", f"/api/studies/{b_id}/json"):
        assert client.get(path, headers=a_headers).status_code == 404
    assert client.delete(f"/api/studies/{b_id}", headers=a_headers).status_code == 404
    first = client.get(f"/api/studies/{a_id}/json", headers=a_headers)
    second = client.get(f"/api/studies/{a_id}/json", headers=a_headers)
    assert first.status_code == 200 and first.content == second.content               # same study, same bytes
    assert first.headers["content-disposition"].startswith('attachment; filename="study-nifty-bank-')
    raw = first.content.decode()
    doc = json.loads(raw)
    assert doc["format"] == store.EXPORT_FORMAT and doc["information"] == S.INFO_LINE
    assert doc["study"]["data"]["notes"]["text"] == "alpha private note"
    for leak in ("bravo private note", b_email, a_email, "broker_account_identifier", "account_identifier", "zk-secret",
                 '"user_id"', '"tenant_id"', '"id"'):
        assert leak not in raw
    assert S.fingerprint(S.canonical(doc["study"])) == doc["fingerprint"]


def test_search_by_symbol_and_ist_date_then_delete(served):
    headers, _, _ = _member("search")
    sid = client.post("/api/studies", json=BODY, headers=headers).json()["id"]
    ist_today = datetime.now(store.IST).date()
    assert [i["id"] for i in client.get("/api/studies", params={"symbol": "nifty"}, headers=headers).json()["items"]] == [sid]
    assert client.get("/api/studies", params={"symbol": "RELIANCE"}, headers=headers).json()["items"] == []
    assert client.get("/api/studies", params={"date_from": ist_today.isoformat(), "date_to": ist_today.isoformat()},
                      headers=headers).json()["total"] == 1
    assert client.get("/api/studies", params={"date_to": (ist_today - timedelta(days=1)).isoformat()}, headers=headers).json()["total"] == 0
    assert client.get("/api/studies", params={"symbol": "%"}, headers=headers).json()["total"] == 0          # a literal, not a wildcard
    assert client.delete(f"/api/studies/{sid}", headers=headers).json() == {"deleted": True}
    assert client.get(f"/api/studies/{sid}", headers=headers).status_code == 404


def test_plain_refusals_for_bad_input_and_size(served, monkeypatch):
    headers, _, _ = _member("bounds")
    long_notes = "n" * (config.STUDY_NOTES_MAX_CHARS + 1)
    r = client.post("/api/studies", json={**BODY, "notes": long_notes}, headers=headers)
    assert r.status_code == 422 and r.json()["detail"] == "Your notes are longer than 5,000 characters. Shorten them, then save again."
    r = client.post("/api/studies", json={**BODY, "timeframes": ["15m", "15m"]}, headers=headers)
    assert r.status_code == 422 and r.json()["detail"] == "Choose each timeframe once."
    r = client.post("/api/studies", json={**BODY, "instrument": "*"}, headers=headers)
    assert r.status_code == 422 and "Open a chart" in r.json()["detail"]
    monkeypatch.setattr(config, "STUDY_SNAPSHOT_MAX_BYTES", 2048)
    r = client.post("/api/studies", json=BODY, headers=headers)
    assert r.status_code == 413 and r.json()["detail"].startswith("This study is too large to save")
    assert client.get("/api/studies", headers=headers).json()["total"] == 0                                  # nothing stored


def test_studies_are_in_download_my_data_and_go_with_the_account(served):
    headers, me, _ = _member("zip")
    client.post("/api/studies", json={**BODY, "notes": "zip note"}, headers=headers)
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/me/export", headers=headers).content))
    rows = json.loads(z.read("studies.json"))
    assert len(rows) == 1 and json.loads(rows[0]["snapshot_json"])["data"]["notes"]["text"] == "zip note"
    assert "studies.csv" in z.namelist()

    from sqlalchemy import func, select

    from app.db.models import CopilotStudyRecord, User
    from app.retention.service import erase_user
    from tests.test_auth_api import _session_factory
    from tests.test_phase_l_ai import _run

    async def go():
        async with _session_factory() as session:
            user = await session.get(User, me["id"])
            await erase_user(session, user, actor_id=None)
            await session.commit()
            return await session.scalar(select(func.count()).select_from(CopilotStudyRecord).where(CopilotStudyRecord.user_id == me["id"]))
    assert _run(go()) == 0


def test_routes_are_classified_for_tenancy_and_the_migration_is_rollback_safe():
    from tests.tenancy_route_table import ROUTE_TABLE
    for method, path in (("POST", "/api/studies"), ("GET", "/api/studies"), ("GET", "/api/studies/{study_id}"),
                         ("GET", "/api/studies/{study_id}/json"), ("DELETE", "/api/studies/{study_id}")):
        assert ROUTE_TABLE[(method, path)].startswith(("member", "self"))
    text = (Path(__file__).resolve().parents[1] / "alembic" / "versions" / "c4e7a9b1d3f5_m77_copilot_studies.py").read_text()
    assert "ROLLBACK_SAFE = True" in text and "down_revision: str | Sequence[str] | None = 'c4e8a2f6b031'" in text

"""FN2-i: GET /api/screener/runs - the caller's own past runs, newest first (never another member's or another
organisation's); a screen_id that is not the organisation's answers 404; flagged with the screener."""
import json

from app.db.models import ScreenRecord, ScreenRunRecord
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner
from tests.test_s1d_screener_api import _run, flag_on  # noqa: F401 - fixture


def _add_run(tenant_id, user_id, matched, screen_id=None, base_tf="1d"):
    async def go():
        async with _session_factory() as session:
            results = [{"symbol": s, "matched": True, "reason": None} for s in matched] + [{"symbol": "ZZZ", "matched": False, "reason": None}]
            row = ScreenRunRecord(tenant_id=tenant_id, user_id=user_id, screen_id=screen_id, ast_sha256="0" * 64, ast_version="screenql/1",
                                  base_tf=base_tf, universe_json=json.dumps(matched + ["ZZZ"]), data_source="broker:fake",
                                  scanned=len(results), matched=len(matched), result_json=json.dumps(results))
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def _add_screen(tenant_id, user_id, name):
    async def go():
        async with _session_factory() as session:
            row = ScreenRecord(tenant_id=tenant_id, created_by=user_id, name=name, source_text="close > 1", ast_json="{}")
            session.add(row)
            await session.commit()
            return row.id
    return _run(go())


def test_the_list_is_flagged_with_the_screener():
    headers, _ = _owner("fn2i-off@example.com")
    r = client.get("/api/screener/runs", headers=headers)
    assert r.status_code == 503 and r.headers.get("X-Feature-Disabled") == "screener_v2"


def test_only_the_callers_own_runs_newest_first(flag_on):  # noqa: F811
    headers, me = _owner("fn2i-me@example.com")
    other, them = _owner("fn2i-other@example.com")
    sid = _add_screen(me["tenant_id"], me["id"], "Trend up")
    first = _add_run(me["tenant_id"], me["id"], ["A", "B"])
    second = _add_run(me["tenant_id"], me["id"], ["C", "D", "E", "F", "G", "H"], screen_id=sid, base_tf="1w")
    _add_run(me["tenant_id"], them["id"], ["X"])           # another member's run in the same organisation
    _add_run(me["tenant_id"], None, ["Y"])                 # a run whose user is gone
    theirs = _add_run(them["tenant_id"], them["id"], ["Q"])

    body = client.get("/api/screener/runs", headers=headers).json()
    assert [r["run_id"] for r in body["runs"]] == [second, first]
    top = body["runs"][0]
    assert top["screen_name"] == "Trend up" and top["base_tf"] == "1w" and top["matched"] == 6 and top["scanned"] == 7
    assert top["first_matches"] == ["C", "D", "E", "F", "G"] and top["created_at"]
    assert body["runs"][1]["screen_name"] is None and body["runs"][1]["first_matches"] == ["A", "B"]
    assert "recommendation" in body["disclaimer"]
    assert "results" not in top and "universe" not in top

    assert [r["run_id"] for r in client.get("/api/screener/runs?limit=1", headers=headers).json()["runs"]] == [second]
    assert [r["run_id"] for r in client.get(f"/api/screener/runs?screen_id={sid}", headers=headers).json()["runs"]] == [second]

    mine_seen_by_them = client.get("/api/screener/runs", headers=other).json()["runs"]
    assert [r["run_id"] for r in mine_seen_by_them] == [theirs]
    assert client.get(f"/api/screener/runs?screen_id={sid}", headers=other).status_code == 404   # not their organisation's screen
    assert client.get(f"/api/screener/runs/{first}", headers=other).status_code == 404           # reopen: another organisation's run
    assert client.get("/api/screener/runs?screen_id=999999", headers=headers).status_code == 404

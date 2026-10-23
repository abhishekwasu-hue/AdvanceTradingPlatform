"""LAB-18 (AMW-7, owner decision 2026-10-23): immutable server-side versions of a saved screen - every save that changes
it writes a version, the PUT still updates the current copy, at most 50 per screen (oldest pruned), list and read
routes, no restore-in-place, tenant isolation, and the one idempotent upload of a device's history; the additive
migration c6e8a0b2d4f7."""
import asyncio
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import ScreenRecord, ScreenVersionRecord
from app.screener import versions as sv
from tests.test_auth_api import _session_factory, client  # noqa: F401 - fixtures
from tests.test_phase_l_ai import _owner


def _run(coro):
    return asyncio.run(coro)


def _flags(**on):
    async def go():
        from app.platform import controls
        async with _session_factory() as session:
            current = await controls._get(session, controls.KEY_FEATURE_FLAGS)
            flags = dict(current.get("flags") or {})
            for key, value in on.items():
                flags[key] = {"on": value, "tenants": []}
            await controls._set(session, controls.KEY_FEATURE_FLAGS, {"flags": flags}, None)
            await session.commit()
    _run(go())


@pytest.fixture
def flags_on():
    _flags(screener_v2=True, screener_copilot=True)
    yield
    _flags(screener_v2=False, screener_copilot=False)


def _builder(texts, summaries):
    return {"stages": [{"id": f"s{i}", "kind": "formula", "text": t, "enabled": True} for i, t in enumerate(texts)],
            "texts": texts, "summaries": summaries}


def _save(headers, name, source, base_tf="1d", sid=None, builder=None):
    body = {"name": name, "source": source, "base_tf": base_tf}
    if builder is not None:
        body["builder"] = builder
    if sid is None:
        res = client.post("/api/screener/screens", json=body, headers=headers)
        assert res.status_code == 201, res.text
    else:
        res = client.put(f"/api/screener/screens/{sid}", json=body, headers=headers)
        assert res.status_code == 200, res.text
    return res.json()


def _versions(headers, sid):
    res = client.get(f"/api/screener/screens/{sid}/versions", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _count(sid):
    async def go():
        async with _session_factory() as session:
            return await session.scalar(select(func.count()).select_from(ScreenVersionRecord).where(ScreenVersionRecord.screen_id == sid))
    return _run(go())


def test_routes_are_off_and_saves_write_no_version_while_the_flag_is_off():
    _flags(screener_v2=True, screener_copilot=False)
    try:
        headers, _ = _owner("lab18-off@example.com")
        sid = _save(headers, "Off", "close > 1")["id"]
        res = client.get(f"/api/screener/screens/{sid}/versions", headers=headers)
        assert res.status_code == 503 and res.headers.get("X-Feature-Disabled") == "screener_copilot"
        assert client.post(f"/api/screener/screens/{sid}/versions/import", json={"versions": []}, headers=headers).status_code == 503
        assert _count(sid) == 0
    finally:
        _flags(screener_v2=False, screener_copilot=False)


def test_every_save_that_changes_the_scan_writes_an_immutable_version(flags_on):
    headers, _ = _owner("lab18-save@example.com")
    made = _save(headers, "Trend", "close > SMA(close, 20)", builder=_builder(["close > SMA(close, 20)"], ["Close above its 20-bar average"]))
    sid = made["id"]
    same = _save(headers, "Trend", "close > SMA(close, 20)", sid=sid)            # nothing changed: no new version
    assert same["text"] == "close > SMA(close, 20)" and _count(sid) == 1
    _save(headers, "Trend", "close > SMA(close, 20) AND RSI(14) > 50", sid=sid,
          builder=_builder(["close > SMA(close, 20)", "RSI(14) > 50"], ["Close above its 20-bar average", "RSI(14) above 50"]))
    _save(headers, "Trend weekly", "close > SMA(close, 20)", base_tf="1w", sid=sid)      # no builder: counts only
    listed = _versions(headers, sid)
    rows = listed["versions"]
    assert [r["number"] for r in rows] == [1, 2, 3] and [r["version"] for r in rows] == [1, 2, 3]
    assert [r["latest"] for r in rows] == [False, False, True] and listed["max"] == 50 and listed["imported"] is False
    assert rows[0]["change"] == ["First saved version."]
    assert rows[1]["change"] == ["Added: RSI(14) above 50"] and rows[1]["conditions"] == 2
    assert rows[2]["change"] == ["Removed 1 condition.", "Timeframe: 1d to 1w", "Name: “Trend” to “Trend weekly”"]
    assert all(r["origin"] == "save" for r in rows)
    # the PUT updated the current copy in place; the old version is still there, unchanged
    current = client.get("/api/screener/screens", headers=headers).json()[0]
    assert current["id"] == sid and current["name"] == "Trend weekly" and current["base_tf"] == "1w"
    one = client.get(f"/api/screener/screens/{sid}/versions/2", headers=headers).json()
    assert one["text"] == "close > SMA(close, 20) AND RSI(14) > 50" and one["base_tf"] == "1d" and one["params"] == {}
    assert one["builder"]["summaries"] == ["Close above its 20-bar average", "RSI(14) above 50"] and one["number"] == 2
    assert client.get(f"/api/screener/screens/{sid}/versions/9", headers=headers).status_code == 404


def test_there_is_no_restore_in_place_and_saving_an_older_version_writes_a_new_one(flags_on):
    headers, _ = _owner("lab18-restore@example.com")
    sid = _save(headers, "A", "close > 1")["id"]
    _save(headers, "A", "close > 2", sid=sid)
    from app.screener.routes import router as screens_router
    from app.screener.version_routes import router
    paths = {(m, r.path) for r in [*router.routes, *screens_router.routes] for m in r.methods if "/versions" in r.path}
    assert paths == {("GET", "/api/screener/screens/{screen_id}/versions"), ("GET", "/api/screener/screens/{screen_id}/versions/{version}"),
                     ("POST", "/api/screener/screens/{screen_id}/versions/import")}
    old = client.get(f"/api/screener/screens/{sid}/versions/1", headers=headers).json()
    _save(headers, old["name"], old["text"], old["base_tf"], sid=sid)          # "open version 1" then Save
    rows = _versions(headers, sid)["versions"]
    assert [r["version"] for r in rows] == [1, 2, 3] and rows[2]["latest"]
    assert client.get(f"/api/screener/screens/{sid}/versions/3", headers=headers).json()["text"] == "close > 1"


def test_at_most_fifty_versions_per_scan_and_the_oldest_are_pruned(flags_on):
    headers, _ = _owner("lab18-cap@example.com")
    sid = _save(headers, "Cap", "close > 0")["id"]
    for i in range(1, sv.MAX_VERSIONS + 5):
        _save(headers, "Cap", f"close > {i}", sid=sid)
    rows = _versions(headers, sid)["versions"]
    assert len(rows) == sv.MAX_VERSIONS == _count(sid)
    assert rows[0]["version"] == 6 and rows[-1]["version"] == sv.MAX_VERSIONS + 5 and rows[0]["number"] == 1
    assert client.get(f"/api/screener/screens/{sid}/versions/1", headers=headers).status_code == 404


def test_another_organisation_never_sees_or_imports_into_a_scan(flags_on):
    headers, _ = _owner("lab18-tenant@example.com")
    other, _ = _owner("lab18-tenant-other@example.com")
    sid = _save(headers, "Mine", "close > 1")["id"]
    assert client.get(f"/api/screener/screens/{sid}/versions", headers=other).status_code == 404
    assert client.get(f"/api/screener/screens/{sid}/versions/1", headers=other).status_code == 404
    res = client.post(f"/api/screener/screens/{sid}/versions/import", json={"versions": []}, headers=other)
    assert res.status_code == 404
    assert _versions(headers, sid)["imported"] is False and _count(sid) == 1


def _device(at, name, base_tf, texts, summaries):
    return {"at": at.isoformat().replace("+00:00", "Z"), "name": name, "base_tf": base_tf, "texts": texts,
            "summaries": summaries, "stages": [{"id": f"d{i}", "kind": "formula", "text": t, "enabled": True} for i, t in enumerate(texts)]}


def test_the_device_history_is_uploaded_once_validated_and_placed_before_the_server_versions(flags_on):
    headers, _ = _owner("lab18-import@example.com")
    sid = _save(headers, "Swing", "close > SMA(close, 50) AND RSI(14) > 60")["id"]        # server version 1 (now)
    t0 = datetime.now(timezone.utc) - timedelta(days=3)
    items = [
        _device(t0 + timedelta(hours=2), "Swing", "1d", ["close > SMA(close, 50)", "RSI(14) > 60"], ["Close above its 50-bar average", "RSI(14) above 60"]),
        _device(t0, "Swing", "1d", ["close > SMA(close, 50)"], ["Close above its 50-bar average"]),     # out of order: sorted
        _device(t0 + timedelta(hours=1), "Swing", "1d", ["close > > 1"], ["broken"]),                   # does not compile
        _device(t0 + timedelta(hours=3), "Swing", "1d", ["close > SMA(close, 50)", "RSI(14) > 60"], ["a", "b"]),  # repeat
        _device(datetime.now(timezone.utc) + timedelta(days=1), "Later", "1d", ["close > 1"], ["x"]),   # after the server's
        {"at": "not a time", "name": "x", "base_tf": "1d", "texts": ["close > 1"]},
        _device(t0, "Swing", "7d", ["close > 1"], ["x"]),                                               # unknown timeframe
    ]
    res = client.post(f"/api/screener/screens/{sid}/versions/import", json={"versions": items}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json() == {"imported": 2, "skipped": 5, "already": False}
    rows = _versions(headers, sid)["versions"]
    assert [r["origin"] for r in rows] == ["device", "device", "save"] and [r["number"] for r in rows] == [1, 2, 3]
    assert [r["version"] for r in rows] == [2, 3, 1]                   # numbers are never reused; order is by save time
    assert rows[0]["change"] == ["First saved version."] and rows[1]["change"] == ["Added: RSI(14) above 60"]
    assert rows[2]["latest"] and _versions(headers, sid)["imported"] is True
    one = client.get(f"/api/screener/screens/{sid}/versions/3", headers=headers).json()
    assert one["text"] == "close > SMA(close, 50) AND RSI(14) > 60" and one["builder"]["texts"] == ["close > SMA(close, 50)", "RSI(14) > 60"]
    # idempotent: the marker makes a second upload (another tab, a retry) a no-op
    again = client.post(f"/api/screener/screens/{sid}/versions/import", json={"versions": items}, headers=headers)
    assert again.json() == {"imported": 0, "skipped": 0, "already": True} and _count(sid) == 3


def test_the_upload_is_capped_and_a_scan_without_server_versions_starts_at_one(flags_on):
    headers, _ = _owner("lab18-import-cap@example.com")
    _flags(screener_copilot=False)
    sid = _save(headers, "Old", "close > 0")["id"]                     # saved before the feature: no server version
    _flags(screener_copilot=True)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    many = [_device(t0 + timedelta(minutes=i), "Old", "1d", [f"close > {i}"], [f"Close above {i}"]) for i in range(sv.MAX_IMPORT + 1)]
    assert client.post(f"/api/screener/screens/{sid}/versions/import", json={"versions": many}, headers=headers).status_code == 422
    res = client.post(f"/api/screener/screens/{sid}/versions/import", json={"versions": many[:sv.MAX_IMPORT]}, headers=headers).json()
    assert res == {"imported": sv.MAX_IMPORT, "skipped": 0, "already": False}
    rows = _versions(headers, sid)["versions"]
    assert [r["version"] for r in rows] == list(range(1, sv.MAX_IMPORT + 1))
    assert rows[1]["change"] == ["Added: Close above 1", "Removed: Close above 0"]
    _save(headers, "Old", "close > 99", sid=sid)                       # the next save continues the numbering
    assert _versions(headers, sid)["versions"][-1]["version"] == sv.MAX_IMPORT + 1


def test_a_viewer_reads_versions_but_cannot_upload():
    # the tenancy sweep checks every route's class; this pins the intent for the two kinds
    from tests.tenancy_route_table import ROUTE_TABLE as ROUTES
    assert ROUTES[("GET", "/api/screener/screens/{screen_id}/versions")] == "member"
    assert ROUTES[("GET", "/api/screener/screens/{screen_id}/versions/{version}")] == "member"
    assert ROUTES[("POST", "/api/screener/screens/{screen_id}/versions/import")] == "trader"


def test_helpers_clean_the_builder_and_describe_changes_in_plain_words():
    assert sv.clean_builder(None) is None and sv.clean_builder({"stages": [], "texts": ["a"], "summaries": []}) is None
    assert sv.clean_builder({"stages": [], "texts": [1], "summaries": ["x"]}) is None
    assert sv.clean_builder({"stages": [{"x": "y" * sv.MAX_BUILDER}], "texts": [], "summaries": []}) is None
    tidy = sv.clean_builder({"stages": [], "texts": ["a"], "summaries": ["two\nlines\x07" + "z" * 400], "extra": 1})
    assert tidy is not None and set(tidy) == {"stages", "texts", "summaries"} and "\n" not in tidy["summaries"][0]
    assert len(tidy["summaries"][0]) <= sv.MAX_LINE
    a = sv.Body("A", "close > 1", '{"t": "x"}', "screenql/1", "1d", "{}")
    assert sv.describe_change(None, None, a, None) == "First saved version."
    assert sv.describe_change(a, None, a, None) == "No change in the conditions."
    b = sv.Body("A", "close > 1", '{"t": "x"}', "screenql/1", "1d", '{"n": 2}')
    assert sv.describe_change(a, None, b, None) == "Changed a setting." and a.sha256() != b.sha256()
    for line in ["First saved version.", "Added 1 condition.", "Removed 2 conditions.", "Changed a setting."]:
        assert not any(w in line.lower() for w in ("buy", "sell", "target", "stop"))


def test_versions_are_tenant_scoped_and_deleted_with_their_screen():
    table = ScreenVersionRecord.__table__
    fks = {fk.parent.name: (fk.column.table.name, fk.ondelete) for fk in table.foreign_keys}
    assert fks == {"tenant_id": ("tenants", "CASCADE"), "screen_id": ("screens", "CASCADE"), "created_by": ("users", "SET NULL")}
    assert {tuple(c.name for c in u.columns) for u in table.constraints if u.__class__.__name__ == "UniqueConstraint"} == {("screen_id", "version")}
    assert {i.name for i in table.indexes} == {"ix_screen_versions_tenant_id", "ix_screen_versions_screen_created"}
    assert ScreenRecord.__table__.c.versions_imported_at.nullable


def test_migration_is_additive_rollback_safe_reversible_and_chained_on_cp6_15():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from scripts.migration_rollback_safety import lint, risky_operations

    root = Path(__file__).resolve().parents[1]
    path = root / "alembic" / "versions" / "c6e8a0b2d4f7_lab18_screen_versions.py"
    spec = importlib.util.spec_from_file_location("lab18_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ROLLBACK_SAFE is True and module.revision == "c6e8a0b2d4f7" and module.down_revision == "b5d7f9a1c3e6"
    source = path.read_text(encoding="utf-8")
    assert risky_operations(source) == [] and not hasattr(module, "ROLLBACK_SAFE_REASON")
    assert not [p for p in lint() if p.startswith(path.name)]
    assert "ondelete='CASCADE'" in source and "UniqueConstraint('screen_id', 'version'" in source
    assert "'versions_imported_at', sa.DateTime(timezone=True), nullable=True" in source
    assert "drop_table('screen_versions')" in source and "drop_column('screens', 'versions_imported_at')" in source
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1 and "c6e8a0b2d4f7" in {r.revision for r in script.walk_revisions("base", heads[0])}

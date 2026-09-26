"""Phase D2: compliance exports - tenant-scoped owner exports, platform-wide admin exports,
CSV/JSON shapes, date ranges, SHA-256 manifest, chain verdict, and the export audit row."""
import csv
import hashlib
import io
import json
from datetime import date, timedelta

from tests.test_admin_api import _admin
from tests.test_auth_api import client
from tests.test_team_api import _join, _owner


def _rows(csv_text: str):
    data = [line for line in csv_text.splitlines() if not line.startswith("#")]
    manifest = dict(line[2:].split("=", 1) for line in csv_text.splitlines() if line.startswith("#"))
    return list(csv.DictReader(io.StringIO("\n".join(data)))), {k: json.loads(v) for k, v in manifest.items()}


def test_owner_exports_tenant_audit_logs_as_csv_with_manifest_and_hash():
    headers, me = _owner("export-owner@example.com")
    resp = client.get("/api/exports/audit-logs?format=csv", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'filename="audit-logs_tenant' in resp.headers["content-disposition"]
    assert resp.headers["x-content-sha256"] == hashlib.sha256(resp.content).hexdigest()
    # Other test modules deliberately tamper with the shared chain, so only consistency between
    # header and manifest is asserted here; the verdict itself is covered by test_audit_log_chain.
    assert resp.headers["x-audit-chain-intact"] in ("true", "false")

    rows, manifest = _rows(resp.text)
    assert rows and all(r["tenant_id"] == str(me["tenant_id"]) for r in rows)
    assert {"prev_hash", "hash", "event", "user_email"} <= set(rows[0])
    assert any(r["event"] == "user_registered" or "register" in r["event"] for r in rows)
    assert manifest["dataset"] == "audit-logs" and manifest["scope"] == f"tenant:{me['tenant_id']}"
    assert manifest["row_count"] == len(rows) == int(resp.headers["x-export-rows"])
    assert manifest["audit_chain_intact_at_export"] is (resp.headers["x-audit-chain-intact"] == "true")

    # The export itself is on the trail.
    again = client.get("/api/exports/audit-logs?format=json", headers=headers).json()
    events = [r["event"] for r in again["rows"]]
    assert "export_generated" in events
    detail = next(r["detail"] for r in again["rows"] if r["event"] == "export_generated")
    assert detail.startswith("audit-logs csv tenant") and "sha256=" in detail


def test_json_export_has_manifest_and_rows_and_respects_date_range():
    headers, me = _owner("export-dates@example.com")
    # Registration is not a login attempt; make one so the dataset has a row today.
    assert client.post("/api/auth/login", json={"email": "export-dates@example.com", "password": "S3cur3Pass!"}).status_code == 200
    today = date.today()
    resp = client.get(f"/api/exports/login-events?format=json&from={today}&to={today}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["manifest"]["from"] == today.isoformat() and body["manifest"]["to"] == today.isoformat()
    assert body["rows"] and all(r["email"] == "export-dates@example.com" for r in body["rows"])

    past = today - timedelta(days=30)
    empty = client.get(f"/api/exports/login-events?format=json&from={past}&to={past}", headers=headers).json()
    assert empty["rows"] == [] and empty["manifest"]["row_count"] == 0

    bad = client.get(f"/api/exports/login-events?from={today}&to={past}", headers=headers)
    assert bad.status_code == 400


def test_exports_are_owner_only_and_never_cross_tenants():
    owner_headers, owner = _owner("export-a@example.com")
    member_headers, _ = _join(owner_headers, "export-a-member@example.com", role="USER")
    assert client.get("/api/exports/orders", headers=member_headers).status_code == 403

    other_headers, other = _owner("export-b@example.com")
    body = client.get("/api/exports/audit-logs?format=json", headers=other_headers).json()
    assert body["rows"] and all(r["tenant_id"] == other["tenant_id"] for r in body["rows"])
    assert not any(r["user_email"] == "export-a@example.com" for r in body["rows"])


def test_unknown_dataset_and_format_are_rejected():
    headers, _ = _owner("export-bad@example.com")
    assert client.get("/api/exports/secrets", headers=headers).status_code == 404
    assert client.get("/api/exports/orders?format=xml", headers=headers).status_code == 400


def test_admin_exports_platform_wide_or_one_tenant():
    admin_headers, admin = _admin("export-admin@example.com")
    owner_headers, owner = _owner("export-victim@example.com")

    everything = client.get("/api/admin/exports/audit-logs?format=json", headers=admin_headers).json()
    tenants = {r["tenant_id"] for r in everything["rows"]}
    assert owner["tenant_id"] in tenants and admin["tenant_id"] in tenants
    assert everything["manifest"]["scope"] == "platform"

    one = client.get(f"/api/admin/exports/trades?format=csv&tenant_id={owner['tenant_id']}", headers=admin_headers)
    assert one.status_code == 200
    rows, manifest = _rows(one.text)
    assert manifest["scope"] == f"tenant:{owner['tenant_id']}"

    # Owners cannot reach the platform export.
    assert client.get("/api/admin/exports/audit-logs", headers=owner_headers).status_code == 403


def test_orders_export_includes_algo_tag_column():
    headers, _ = _owner("export-orders@example.com")
    resp = client.get("/api/exports/orders?format=csv", headers=headers)
    header = resp.text.splitlines()[0].split(",")
    assert "algo_tag" in header and "broker_order_id" in header and "reasons" in header

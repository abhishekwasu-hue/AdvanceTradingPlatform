"""AUD-G1.7 (owner audit 28 Oct): the platform owner's Compliance page - SUPER_ADMIN only, read only."""
from app.brokers.catalog import catalog
from app.compliance import golive
from tests.test_auth_api import client
from tests.test_d6_golive import _admin, _owner


def test_only_the_platform_admin_reads_it_and_it_lists_every_broker():
    owner, _ = _owner("g17-owner@example.com")
    assert client.get("/api/compliance/overview", headers=owner).status_code == 403
    admin, _ = _admin("g17-admin@example.com")
    body = client.get("/api/compliance/overview", headers=admin).json()
    assert {b["broker"] for b in body["brokers"]} == {b["broker"] for b in catalog()}
    assert {i["id"] for i in body["golive"]} == {i["id"] for i in golive.items()}
    assert set(body["exports"]) >= {"audit-logs", "orders", "trades"}
    assert {"server_egress_ip", "static_ip_required_for_live", "ops_within_threshold", "orgs_with_algo_id"} <= set(body)
    assert all({"tag_format", "generic_algo_id", "orgs_with_static_ip"} <= set(b) for b in body["brokers"])

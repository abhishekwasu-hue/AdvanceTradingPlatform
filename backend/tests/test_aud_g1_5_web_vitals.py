"""AUD-G1.5 (owner audit 28 Oct): real-browser Core Web Vitals reach /metrics with closed labels only."""
from app.observability.metrics import WEB_VITAL_LCP
from tests.test_auth_api import client


def _count(page: str, device: str) -> float:
    for metric in WEB_VITAL_LCP.collect():
        for s in metric.samples:
            if s.name.endswith("_count") and s.labels == {"page": page, "device": device}:
                return s.value
    return 0.0


def test_a_page_view_is_observed_and_bad_input_is_refused():
    before = _count("today", "phone")
    assert client.post("/api/pwa/vitals", json={"page": "today", "device": "phone", "lcp_ms": 1800, "inp_ms": 120, "cls": 0.02}).status_code == 204
    assert _count("today", "phone") == before + 1
    assert client.post("/api/pwa/vitals", json={"page": "https://evil/x", "lcp_ms": 1}).status_code == 422     # closed label
    assert client.post("/api/pwa/vitals", json={"page": "today", "lcp_ms": 999_999}).status_code == 422       # bounded
    assert client.post("/api/pwa/vitals", json={"page": "today", "email": "a@b.c"}).status_code == 422       # nothing else

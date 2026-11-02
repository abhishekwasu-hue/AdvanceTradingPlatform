"""M76 MP-0: one order ticket's report reaches /metrics with closed labels and bounded counts only, and nothing else.

The report is the baseline of today's tickets (docs/design/M76_REVIEW.md section 8): cards / lines shown at Place and the
time to Place. It is logged in, stores nothing and touches no order path (the ticket never waits for it).
"""
from app.core import rate_limit
from app.observability.metrics import TICKET_CARDS, TICKET_CHECKS, TICKET_LINES, TICKET_PENDING_AT_PLACE, TICKET_REPORTS, TICKET_TO_PLACE
from tests.test_auth_api import _register, client

URL = "/api/ui/ticket-metrics"
PLACED = {"surface": "ticket", "device": "phone", "outcome": "placed", "cards_shown": 2, "lines_total": 3, "lines_visible": 3,
          "ms_open_to_place": 917, "ms_review_to_place": 561, "checks_ms": 240, "pending_at_place": 0}


def _headers(email: str) -> dict:
    return {"Authorization": f"Bearer {_register(email)}"}


def _sample(metric, suffix: str, labels: dict) -> float:
    for m in metric.collect():
        for s in m.samples:
            if s.name.endswith(suffix) and s.labels == labels:
                return s.value
    return 0.0


def _bucket(metric, labels: dict, le: str) -> float:
    return _sample(metric, "_bucket", {**labels, "le": le})


def test_a_placed_ticket_is_counted_with_its_cards_lines_and_times():
    rate_limit.reset("ticket_metrics")
    h = _headers("m76-mp0-placed@example.com")
    reports = _sample(TICKET_REPORTS, "_total", {"surface": "ticket", "device": "phone", "outcome": "placed"})
    cards_n = _sample(TICKET_CARDS, "_count", {"surface": "ticket", "outcome": "placed"})
    cards_sum = _sample(TICKET_CARDS, "_sum", {"surface": "ticket", "outcome": "placed"})
    lines_sum = _sample(TICKET_LINES, "_sum", {"surface": "ticket", "kind": "total"})
    to_place = {"surface": "ticket", "device": "phone", "span": "open_to_place"}
    under_1s = _bucket(TICKET_TO_PLACE, to_place, "1.0")
    checks = {"surface": "ticket"}
    within_budget = _bucket(TICKET_CHECKS, checks, "0.3")

    r = client.post(URL, headers=h, json=PLACED)

    assert r.status_code == 204 and r.content == b""
    assert _sample(TICKET_REPORTS, "_total", {"surface": "ticket", "device": "phone", "outcome": "placed"}) == reports + 1
    assert _sample(TICKET_CARDS, "_count", {"surface": "ticket", "outcome": "placed"}) == cards_n + 1
    assert _sample(TICKET_CARDS, "_sum", {"surface": "ticket", "outcome": "placed"}) == cards_sum + 2   # average = sum / count
    assert _sample(TICKET_LINES, "_sum", {"surface": "ticket", "kind": "total"}) == lines_sum + 3
    assert _bucket(TICKET_TO_PLACE, to_place, "1.0") == under_1s + 1                                  # 0.917 s
    assert _bucket(TICKET_CHECKS, checks, "0.3") == within_budget + 1                                  # 240 ms: inside the 300 ms budget


def test_the_300ms_budget_is_a_bucket_edge_so_over_budget_checks_are_readable():
    rate_limit.reset("ticket_metrics")
    h = _headers("m76-mp0-budget@example.com")
    labels = {"surface": "basket"}
    within, all_ = _bucket(TICKET_CHECKS, labels, "0.3"), _sample(TICKET_CHECKS, "_count", labels)
    for ms in (300, 301, 1200):
        body = {**PLACED, "surface": "basket", "checks_ms": ms}
        assert client.post(URL, headers=h, json=body).status_code == 204
    assert _sample(TICKET_CHECKS, "_count", labels) == all_ + 3
    assert _bucket(TICKET_CHECKS, labels, "0.3") == within + 1          # only 300 ms is within; 301 and 1200 are over


def test_a_ticket_closed_after_review_counts_its_cards_but_no_time_to_place():
    rate_limit.reset("ticket_metrics")
    h = _headers("m76-mp0-closed@example.com")
    closed = {"surface": "ticket", "device": "desktop", "outcome": "closed"}
    before = _sample(TICKET_REPORTS, "_total", closed)
    cards = _sample(TICKET_CARDS, "_sum", {"surface": "ticket", "outcome": "closed"})
    times = _sample(TICKET_TO_PLACE, "_count", {"surface": "ticket", "device": "desktop", "span": "open_to_place"})
    body = {**PLACED, "device": "desktop", "outcome": "closed", "cards_shown": 2, "ms_open_to_place": 5000}
    assert client.post(URL, headers=h, json=body).status_code == 204
    assert _sample(TICKET_REPORTS, "_total", closed) == before + 1
    assert _sample(TICKET_CARDS, "_sum", {"surface": "ticket", "outcome": "closed"}) == cards + 2
    assert _sample(TICKET_TO_PLACE, "_count", {"surface": "ticket", "device": "desktop", "span": "open_to_place"}) == times   # no Place, no time


def test_pending_checks_at_place_are_counted():
    rate_limit.reset("ticket_metrics")
    h = _headers("m76-mp0-pending@example.com")
    before = _sample(TICKET_PENDING_AT_PLACE, "_total", {"surface": "ticket"})
    assert client.post(URL, headers=h, json={**PLACED, "pending_at_place": 2}).status_code == 204
    assert _sample(TICKET_PENDING_AT_PLACE, "_total", {"surface": "ticket"}) == before + 2


def test_login_is_needed_and_nothing_but_closed_labels_and_bounded_numbers_is_taken():
    rate_limit.reset("ticket_metrics")
    assert client.post(URL, json=PLACED).status_code == 401
    h = _headers("m76-mp0-input@example.com")
    assert client.post(URL, headers=h, json={**PLACED, "surface": "https://evil/x"}).status_code == 422   # closed label
    assert client.post(URL, headers=h, json={**PLACED, "outcome": "refused"}).status_code == 422
    assert client.post(URL, headers=h, json={**PLACED, "cards_shown": 51}).status_code == 422            # bounded
    assert client.post(URL, headers=h, json={**PLACED, "cards_shown": -1}).status_code == 422
    assert client.post(URL, headers=h, json={**PLACED, "ms_open_to_place": 3_600_001}).status_code == 422
    assert client.post(URL, headers=h, json={**PLACED, "instrument": "NIFTY 24500 CE"}).status_code == 422   # nothing else
    assert client.post(URL, headers=h, json={**PLACED, "email": "a@b.c"}).status_code == 422


def test_the_report_is_rate_limited_per_user():
    rate_limit.reset("ticket_metrics")
    h = _headers("m76-mp0-rate@example.com")
    codes = [client.post(URL, headers=h, json=PLACED).status_code for _ in range(121)]
    assert codes[:120] == [204] * 120 and codes[120] == 429
    rate_limit.reset("ticket_metrics")

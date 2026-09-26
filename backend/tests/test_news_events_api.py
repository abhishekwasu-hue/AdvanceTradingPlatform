from tests.test_auth_api import _register, client


def _rbi_event(**overrides):
    payload = {
        "category": "RBI_POLICY",
        "headline": "RBI cuts repo rate by 25 bps to 6.00%",
        "description": "MPC votes 5-1 to cut the repo rate, citing softening inflation.",
        "event_date": "2026-02-06",
        "affected_symbols": [],
        "sentiment": "Bullish",
        "source": {"source": "RBI Monetary Policy Statement", "source_url": "https://rbi.org.in/press-release/123"},
    }
    payload.update(overrides)
    return payload


def test_news_events_write_requires_authentication():
    assert client.post("/api/news-events", json=_rbi_event()).status_code in (401, 403)


def test_news_events_read_is_open_without_authentication():
    response = client.get("/api/news-events")
    assert response.status_code == 200


def test_create_requires_a_citation():
    token = _register("news_no_source@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    payload = _rbi_event()
    del payload["source"]
    response = client.post("/api/news-events", headers=headers, json=payload)
    assert response.status_code == 422


def test_create_list_get_delete_news_event():
    token = _register("news_owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create = client.post("/api/news-events", headers=headers, json=_rbi_event())
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["category"] == "RBI_POLICY"
    assert body["sentiment"] == "Bullish"
    assert body["source"]["source"] == "RBI Monetary Policy Statement"
    event_id = body["id"]

    listed = client.get("/api/news-events")
    assert listed.status_code == 200
    assert any(e["id"] == event_id for e in listed.json())

    fetched = client.get(f"/api/news-events/{event_id}")
    assert fetched.status_code == 200
    assert fetched.json()["headline"] == body["headline"]

    other_token = _register("news_other@example.com")
    other_headers = {"Authorization": f"Bearer {other_token}"}
    forbidden = client.delete(f"/api/news-events/{event_id}", headers=other_headers)
    assert forbidden.status_code == 403

    deleted = client.delete(f"/api/news-events/{event_id}", headers=headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/news-events/{event_id}").status_code == 404


def test_get_unknown_event_returns_404():
    assert client.get("/api/news-events/999999").status_code == 404


def test_filter_by_category_symbol_and_since_date():
    token = _register("news_filters@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    rbi = client.post("/api/news-events", headers=headers, json=_rbi_event(event_date="2026-02-06"))
    assert rbi.status_code == 201

    corporate = client.post(
        "/api/news-events",
        headers=headers,
        json=_rbi_event(
            category="CORPORATE",
            headline="Reliance announces buyback",
            event_date="2026-03-01",
            affected_symbols=["RELIANCE"],
            sentiment="Bullish",
            source={"source": "Exchange filing", "source_url": "https://nseindia.com/filing/456"},
        ),
    )
    assert corporate.status_code == 201

    by_category = client.get("/api/news-events", params={"category": "CORPORATE"})
    categories = {e["category"] for e in by_category.json()}
    assert categories == {"CORPORATE"}

    by_symbol = client.get("/api/news-events", params={"symbol": "reliance"})
    assert all("RELIANCE" in e["affected_symbols"] for e in by_symbol.json())
    assert len(by_symbol.json()) >= 1

    since_march = client.get("/api/news-events", params={"since": "2026-03-01"})
    assert all(e["event_date"] >= "2026-03-01" for e in since_march.json())


def test_affected_symbols_default_to_empty_market_wide():
    token = _register("news_marketwide@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    payload = _rbi_event()
    del payload["affected_symbols"]
    create = client.post("/api/news-events", headers=headers, json=payload)
    assert create.status_code == 201
    assert create.json()["affected_symbols"] == []

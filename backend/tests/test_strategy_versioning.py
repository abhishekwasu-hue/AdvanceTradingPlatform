from tests.test_auth_api import _register, client


def _rsi_config(name="My RSI Strategy", period=14):
    return {
        "name": name,
        "timeframe": "1min",
        "long_conditions": [
            {
                "left": {"type": "indicator", "indicator": "RSI", "period": period},
                "operator": "CROSSES_ABOVE",
                "right": {"type": "value", "value": 50},
            }
        ],
        "short_conditions": [],
    }


def test_creating_a_strategy_makes_version_1_live():
    token = _register("caleb@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post("/api/custom-strategies", headers=headers, json=_rsi_config()).json()
    strategy_db_id = created["id"]
    assert created["live_version_id"] is not None

    versions = client.get(f"/api/custom-strategies/{strategy_db_id}/versions", headers=headers).json()
    assert len(versions) == 1
    assert versions[0]["version_number"] == 1
    assert versions[0]["status"] == "LIVE"
    assert versions[0]["source"] == "created"


def test_update_appends_a_new_version_and_archives_the_old_one():
    token = _register("delia@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post("/api/custom-strategies", headers=headers, json=_rsi_config(period=14)).json()
    strategy_db_id = created["id"]

    updated = client.put(
        f"/api/custom-strategies/{strategy_db_id}", headers=headers, json=_rsi_config(name="Updated", period=21),
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["config"]["name"] == "Updated"
    assert body["config"]["long_conditions"][0]["left"]["period"] == 21

    versions = client.get(f"/api/custom-strategies/{strategy_db_id}/versions", headers=headers).json()
    assert len(versions) == 2
    by_version = {v["version_number"]: v for v in versions}
    assert by_version[1]["status"] == "ARCHIVED"
    assert by_version[1]["config"]["long_conditions"][0]["left"]["period"] == 14
    assert by_version[2]["status"] == "LIVE"
    assert by_version[2]["source"] == "user_edit"

    # The strategy's own record (and every execution path reading it) reflects the new version.
    fetched = client.get(f"/api/custom-strategies/{strategy_db_id}", headers=headers).json()
    assert fetched["config"]["name"] == "Updated"


def test_update_rejects_invalid_config_without_creating_a_version():
    token = _register("ezra@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/custom-strategies", headers=headers, json=_rsi_config()).json()
    strategy_db_id = created["id"]

    bad_config = _rsi_config()
    bad_config["long_conditions"][0]["left"]["period"] = 0
    response = client.put(f"/api/custom-strategies/{strategy_db_id}", headers=headers, json=bad_config)
    assert response.status_code == 422

    versions = client.get(f"/api/custom-strategies/{strategy_db_id}/versions", headers=headers).json()
    assert len(versions) == 1


def test_rollback_creates_a_new_version_matching_the_old_one():
    token = _register("flora@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/custom-strategies", headers=headers, json=_rsi_config(period=14)).json()
    strategy_db_id = created["id"]

    client.put(f"/api/custom-strategies/{strategy_db_id}", headers=headers, json=_rsi_config(period=21))
    client.put(f"/api/custom-strategies/{strategy_db_id}", headers=headers, json=_rsi_config(period=30))

    rollback = client.post(f"/api/custom-strategies/{strategy_db_id}/versions/1/rollback", headers=headers)
    assert rollback.status_code == 200, rollback.text
    body = rollback.json()
    assert body["config"]["long_conditions"][0]["left"]["period"] == 14

    versions = client.get(f"/api/custom-strategies/{strategy_db_id}/versions", headers=headers).json()
    assert len(versions) == 4  # 1 (archived), 2 (archived), 3 (archived), 4 (rollback, live)
    by_version = {v["version_number"]: v for v in versions}
    assert by_version[4]["source"] == "rollback"
    assert by_version[4]["status"] == "LIVE"
    assert by_version[4]["config"]["long_conditions"][0]["left"]["period"] == 14
    # Version 1's own row is untouched - only its status changed, never its content.
    assert by_version[1]["config"]["long_conditions"][0]["left"]["period"] == 14
    assert by_version[1]["status"] == "ARCHIVED"
    assert by_version[3]["status"] == "ARCHIVED"


def test_rollback_to_unknown_version_is_404():
    token = _register("gustavo@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/custom-strategies", headers=headers, json=_rsi_config()).json()
    strategy_db_id = created["id"]

    response = client.post(f"/api/custom-strategies/{strategy_db_id}/versions/99/rollback", headers=headers)
    assert response.status_code == 404


def test_versions_are_tenant_scoped():
    token1 = _register("helena@example.com")
    token2 = _register("ignacio@example.com")
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}

    created = client.post("/api/custom-strategies", headers=headers1, json=_rsi_config()).json()
    strategy_db_id = created["id"]

    assert client.get(f"/api/custom-strategies/{strategy_db_id}/versions", headers=headers2).status_code == 404
    assert client.put(
        f"/api/custom-strategies/{strategy_db_id}", headers=headers2, json=_rsi_config(name="Hijacked"),
    ).status_code == 404
    assert client.post(
        f"/api/custom-strategies/{strategy_db_id}/versions/1/rollback", headers=headers2,
    ).status_code == 404


def test_executed_strategy_uses_current_live_version_after_update(monkeypatch):
    from app.strategy_engine.registry import registry
    from tests.utils import decline_then_rally, make_series

    token = _register("juno@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/custom-strategies", headers=headers, json=_rsi_config(period=14)).json()
    strategy_id = created["strategy_id"]

    client.put(f"/api/custom-strategies/{created['id']}", headers=headers, json=_rsi_config(period=21))

    fetched = client.get(f"/api/custom-strategies/{created['id']}", headers=headers).json()
    assert fetched["config"]["long_conditions"][0]["left"]["period"] == 21

    df = make_series(decline_then_rally()).iloc[:43]
    candles = [
        {
            "timestamp": ts.isoformat(),
            "open": row.open, "high": row.high, "low": row.low, "close": row.close, "volume": row.volume,
        }
        for ts, row in df.iterrows()
    ]
    response = client.post(
        f"/api/strategies/{strategy_id}/signal", headers=headers,
        json={"symbol": "NIFTY", "candles": {"1min": candles}},
    )
    assert response.status_code == 200

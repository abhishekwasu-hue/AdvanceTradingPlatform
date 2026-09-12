from tests.test_auth_api import _register, client
from tests.utils import decline_then_rally, make_series


def _rsi_config(name="My RSI Strategy"):
    return {
        "name": name,
        "timeframe": "1min",
        "long_conditions": [
            {
                "left": {"type": "indicator", "indicator": "RSI", "period": 14},
                "operator": "CROSSES_ABOVE",
                "right": {"type": "value", "value": 50},
            }
        ],
        "short_conditions": [],
    }


def _candles_payload(df):
    return [
        {
            "timestamp": ts.isoformat(),
            "open": row.open, "high": row.high, "low": row.low, "close": row.close, "volume": row.volume,
        }
        for ts, row in df.iterrows()
    ]


def test_custom_strategies_require_authentication():
    assert client.post("/api/custom-strategies", json=_rsi_config()).status_code in (401, 403)
    assert client.get("/api/custom-strategies").status_code in (401, 403)


def test_create_list_get_delete_custom_strategy():
    token = _register("oscar@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create = client.post("/api/custom-strategies", headers=headers, json=_rsi_config())
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["strategy_id"].startswith("custom:")
    strategy_db_id = body["id"]

    listing = client.get("/api/custom-strategies", headers=headers).json()
    assert len(listing) == 1
    assert listing[0]["config"]["name"] == "My RSI Strategy"

    fetched = client.get(f"/api/custom-strategies/{strategy_db_id}", headers=headers)
    assert fetched.status_code == 200

    deleted = client.delete(f"/api/custom-strategies/{strategy_db_id}", headers=headers)
    assert deleted.status_code == 204
    assert client.get("/api/custom-strategies", headers=headers).json() == []


def test_create_rejects_invalid_config():
    token = _register("peggy@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    # No long or short conditions at all - CustomStrategyConfig's own validation (model_post_init)
    # rejects this during FastAPI's request-body parsing, before the route body ever runs.
    bad_config = {"name": "Empty", "timeframe": "1min", "long_conditions": [], "short_conditions": []}
    response = client.post("/api/custom-strategies", headers=headers, json=bad_config)
    assert response.status_code == 422


def test_custom_strategy_not_visible_or_usable_by_other_users():
    token_a = _register("quinn@example.com")
    token_b = _register("rose@example.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    created = client.post("/api/custom-strategies", headers=headers_a, json=_rsi_config()).json()
    strategy_id = created["strategy_id"]

    assert client.get("/api/custom-strategies", headers=headers_b).json() == []
    assert client.get(f"/api/strategies/{strategy_id}", headers=headers_b).status_code == 404

    df = make_series(decline_then_rally()).iloc[:43]
    resp = client.post(
        f"/api/strategies/{strategy_id}/signal", headers=headers_b,
        json={"symbol": "NIFTY", "candles": {"1min": _candles_payload(df)}},
    )
    assert resp.status_code == 404


def test_custom_strategy_requires_auth_even_if_id_guessed():
    token = _register("sam@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/custom-strategies", headers=headers, json=_rsi_config()).json()
    strategy_id = created["strategy_id"]

    df = make_series(decline_then_rally()).iloc[:43]
    resp = client.post(
        f"/api/strategies/{strategy_id}/signal",
        json={"symbol": "NIFTY", "candles": {"1min": _candles_payload(df)}},
    )
    assert resp.status_code == 403


def test_custom_strategy_runs_through_signal_enrich_and_backtest():
    token = _register("tina@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/custom-strategies", headers=headers, json=_rsi_config()).json()
    strategy_id = created["strategy_id"]

    assert any(s["id"] == strategy_id for s in client.get("/api/strategies", headers=headers).json())

    df = make_series(decline_then_rally()).iloc[:43]
    candles = _candles_payload(df)

    signal_resp = client.post(
        f"/api/strategies/{strategy_id}/signal", headers=headers,
        json={"symbol": "NIFTY", "candles": {"1min": candles}},
    )
    assert signal_resp.status_code == 200
    assert signal_resp.json()["direction"] == "LONG"

    enrich_resp = client.post(
        f"/api/strategies/{strategy_id}/signal/enrich", headers=headers,
        json={"symbol": "NIFTY", "candles": {"1min": candles}},
    )
    assert enrich_resp.status_code == 200
    assert enrich_resp.json()["signal"]["direction"] == "LONG"

    history = client.get("/api/signal-history", headers=headers).json()
    assert len(history) == 1
    assert history[0]["strategy_id"] == strategy_id

    full_df = make_series(decline_then_rally())
    backtest_resp = client.post(
        "/api/backtest", headers=headers,
        json={
            "strategy_id": strategy_id, "symbol": "NIFTY", "base_timeframe": "1min",
            "candles": _candles_payload(full_df),
        },
    )
    assert backtest_resp.status_code == 200
    assert "total_trades" in backtest_resp.json()

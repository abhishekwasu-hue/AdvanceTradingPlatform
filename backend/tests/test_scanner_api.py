from tests.test_auth_api import client
from tests.utils import make_series


def _candles_payload(df):
    return [
        {
            "timestamp": ts.isoformat(),
            "open": row.open, "high": row.high, "low": row.low, "close": row.close, "volume": row.volume,
        }
        for ts, row in df.iterrows()
    ]


def test_scanner_endpoint_requires_no_authentication_and_filters_symbols():
    rising_df = make_series([100.0 + i for i in range(30)])
    falling_df = make_series([130.0 - i for i in range(30)])

    response = client.post(
        "/api/scanner/run",
        json={
            "symbols": [
                {"symbol": "RISING", "candles": _candles_payload(rising_df)},
                {"symbol": "FALLING", "candles": _candles_payload(falling_df)},
            ],
            "indicator_conditions": [
                {
                    "left": {"type": "indicator", "indicator": "RSI", "period": 14},
                    "operator": "GT",
                    "right": {"type": "value", "value": 60},
                }
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scanned_count"] == 2
    matched_symbols = {m["symbol"] for m in body["matches"]}
    assert "RISING" in matched_symbols
    assert "FALLING" not in matched_symbols


def test_scanner_endpoint_with_no_filters_matches_every_symbol_with_data():
    df = make_series([100.0] * 30)
    response = client.post("/api/scanner/run", json={"symbols": [{"symbol": "ANY", "candles": _candles_payload(df)}]})
    assert response.status_code == 200
    body = response.json()
    assert body["matched_count"] == 1

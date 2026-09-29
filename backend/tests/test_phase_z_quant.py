"""Phase Z: factor scores (raw factors, cross-sectional z-scores, composite with renormalised
weights, buckets, fundamentals), the risk model (correlation, beta, vol, VaR, inverse-vol and
risk-parity weights) and the API including the open-book exposure."""
import numpy as np
import pandas as pd

from app.quant import factors as F, risk as R
from tests.test_auth_api import client
from tests.test_phase_k_commercial import _owner
from tests.utils import make_series


def _series(prices, start="2026-01-02 09:15"):
    df = make_series(list(prices), start=start)
    df["volume"] = 1000.0
    return df


def _trend(n=300, slope=0.15, noise=0.2, seed=1):
    rng = np.random.default_rng(seed)
    idx = np.arange(n)
    return 100 + idx * slope + np.sin(idx / 6) * noise + rng.normal(0, noise / 2, n)


def _candles(df):
    return [{"timestamp": ts.isoformat(), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close), "volume": float(r.volume)}
            for ts, r in df.iterrows()]


def test_raw_factors_and_zscores_behave():
    up, flat, down = _series(_trend(slope=0.2)), _series(100 + np.sin(np.arange(300) / 5) * 0.3), _series(_trend(slope=-0.2, seed=2))
    calm = _series(100 + np.arange(300) * 0.02)
    wild = _series(100 + np.cumsum(np.random.default_rng(5).normal(0, 1.5, 300)))
    raw_up, raw_down, raw_flat = F.raw_factors(up), F.raw_factors(down), F.raw_factors(flat)
    assert raw_up["momentum"] > 0 > raw_down["momentum"] and abs(raw_flat["momentum"]) < abs(raw_up["momentum"])
    assert raw_up["trend"] > 0 > raw_down["trend"]
    assert F.raw_factors(calm)["low_volatility"] > F.raw_factors(wild)["low_volatility"]      # calmer = higher score
    assert raw_up["liquidity"] is not None and raw_up["value"] is None and raw_up["quality"] is None
    with_fund = F.raw_factors(up, {"pe": 20, "pb": 4, "roe_pct": 18, "debt_to_equity": 0.5, "earnings_growth_pct": 12})
    assert with_fund["value"] == (1 / 20 + 1 / 4) / 2 and with_fund["quality"] == 18 + 6 - 5
    short = F.raw_factors(_series(_trend(n=30)))
    assert short["momentum"] is None and short["trend"] is None and short["reversal"] is not None
    z = F.zscores({"A": 1.0, "B": 2.0, "C": 3.0, "D": None, "E": 1000.0, **{f"N{i}": 2.0 + i / 100 for i in range(12)}})
    assert z["D"] is None and z["E"] == 3.0 and z["A"] < z["B"] < z["C"]                  # winsorised at +3
    assert F.zscores({"A": 5.0, "B": 5.0}) == {"A": 0.0, "B": 0.0} and F.zscores({"A": 1.0}) == {"A": 0.0}
    assert F.bars_per_year_of(up) == 250 * 375 and F.bars_per_year_of(make_series([1, 2, 3], freq="1D")) == 250


def test_score_universe_ranks_buckets_and_renormalises_missing_factors():
    inputs = [F.FactorInputs("UP", _series(_trend(slope=0.25, seed=3))), F.FactorInputs("MID", _series(_trend(slope=0.05, seed=4))),
              F.FactorInputs("FLAT", _series(100 + np.sin(np.arange(300) / 4) * 0.4)), F.FactorInputs("DOWN", _series(_trend(slope=-0.2, seed=6))),
              F.FactorInputs("SHORTHIST", _series(_trend(n=100, slope=0.3))), F.FactorInputs("EMPTY", pd.DataFrame())]
    table = F.score_universe(inputs, {"momentum": 0.5, "trend": 0.3, "low_volatility": 0.2, "value": 0.0})
    assert table.weights == {"momentum": 0.5, "trend": 0.3, "low_volatility": 0.2}
    rows = {r.symbol: r for r in table.rows}
    assert "EMPTY" not in rows and any("EMPTY" in w for w in table.warnings)
    assert rows["UP"].rank == 1 and rows["UP"].bucket == "LONG" and rows["DOWN"].bucket == "SHORT"
    assert rows["UP"].composite > rows["MID"].composite > rows["DOWN"].composite
    assert rows["SHORTHIST"].raw["momentum"] is None and rows["SHORTHIST"].composite is not None      # scored on what it has
    assert rows["SHORTHIST"].coverage < rows["UP"].coverage
    assert [r.symbol for r in table.rows][:1] == ["UP"]
    as_dict = table.as_dict()
    assert as_dict["universe"] == 5 and as_dict["factors"][0] == "momentum" and as_dict["rows"][0]["z"]["momentum"] > 0
    tilt = F.exposure({"UP": 0.6, "DOWN": -0.4}, table)
    assert tilt["momentum"] > 0 and tilt["value"] is None
    try:
        F.normalise_weights({"momentum": 0, "trend": 0})
        assert False
    except ValueError:
        pass
    small = F.score_universe(inputs[:2])
    assert all(r.bucket == "NEUTRAL" for r in small.rows) and any("fewer than three" in w for w in small.warnings)


def test_risk_model_correlation_beta_vol_var_and_weights():
    rng = np.random.default_rng(11)
    base = rng.normal(0, 0.01, 400)
    a = 100 * np.exp(np.cumsum(base))
    b = 100 * np.exp(np.cumsum(2 * base))                       # twice the moves of A
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.005, 400)))      # independent, calmer
    frames = {"A": _series(a), "B": _series(b), "C": _series(c)}
    returns = R.returns_matrix(frames, lookback=300)
    assert len(returns) == 300 and list(returns.columns) == ["A", "B", "C"]
    corr = R.correlation(returns)
    assert corr["A"]["A"] == 1.0 and corr["A"]["B"] > 0.99 and abs(corr["A"]["C"]) < 0.3
    beta = R.betas(returns, "A")
    assert abs(beta["B"] - 2.0) < 0.05 and beta["A"] == 1.0
    vols = R.annualised_vol(returns, 250 * 375)
    assert vols["B"] > vols["A"] > vols["C"]
    inv = R.inverse_vol_weights(vols)
    assert abs(sum(inv.values()) - 1) < 1e-6 and inv["C"] > inv["A"] > inv["B"]
    parity = R.risk_parity_weights(returns)
    contrib = R.risk_contributions(returns, parity)
    assert abs(sum(parity.values()) - 1) < 1e-6 and max(contrib.values()) - min(contrib.values()) < 0.05
    port = R.portfolio_risk(returns, {"A": 0.5, "B": 0.5}, 250 * 375)
    assert port["vol_annual"] > 0 and port["var_95"] > 0 and port["cvar_95"] >= port["var_95"] and port["max_drawdown_pct"] >= 0
    diversified = R.portfolio_risk(returns, {"A": 0.5, "C": 0.5}, 250 * 375)
    assert diversified["diversification_ratio"] > port["diversification_ratio"]     # A+C diversify, A+B do not
    summary = R.summarise(frames, weights=None, benchmark="A", lookback=300, bars_per_year=250 * 375)
    assert summary["weights_source"] == "equal" and summary["betas"]["B"] > 1.9 and "disclaimer" in summary
    thin = R.summarise({"A": _series(a[:10])}, weights=None, benchmark="Z", lookback=300, bars_per_year=250 * 375)
    assert any("overlapping bars" in w for w in thin["warnings"]) and any("benchmark" in w for w in thin["warnings"])


def test_quant_endpoints_including_open_book_exposure(monkeypatch):
    up, down, flat = _series(_trend(slope=0.25, seed=7)), _series(_trend(slope=-0.2, seed=8)), _series(100 + np.sin(np.arange(300) / 4) * 0.4)
    symbols = [{"symbol": "up", "candles": _candles(up), "fundamentals": {"pe": 15, "roe_pct": 20}}, {"symbol": "down", "candles": _candles(down)},
               {"symbol": "flat", "candles": _candles(flat)}]
    res = client.post("/api/quant/factors", json={"symbols": symbols, "weights": {"momentum": 0.6, "trend": 0.4}})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["rows"][0]["symbol"] == "UP" and body["rows"][0]["bucket"] == "LONG" and body["weights"] == {"momentum": 0.6, "trend": 0.4}
    assert any("value" in w or "quality" in w for w in body["warnings"]) is False      # UP has fundamentals, so those factors exist
    assert client.post("/api/quant/factors", json={"symbols": symbols, "weights": {"momentum": 0}}).status_code == 400

    risk = client.post("/api/quant/risk", json={"symbols": symbols, "benchmark": "up", "weights": {"up": 0.5, "down": 0.5}})
    assert risk.status_code == 200, risk.text
    r = risk.json()
    assert r["benchmark"] == "UP" and r["weights_source"] == "given" and r["portfolio"]["vol_annual"] > 0
    assert set(r["suggested"]["inverse_volatility"]) == {"UP", "DOWN", "FLAT"} and abs(sum(r["suggested"]["risk_parity"].values()) - 1) < 1e-6
    assert r["betas"]["UP"] == 1.0 and r["correlation"]["UP"]["UP"] == 1.0

    headers, me = _owner("quant-exposure@example.com")
    assert client.post("/api/quant/exposure", json={"symbols": symbols}).status_code == 401
    empty = client.post("/api/quant/exposure", headers=headers, json={"symbols": symbols}).json()
    assert empty["weights"] == {} and any("no open positions" in n for n in empty["notes"])
    given = client.post("/api/quant/exposure", headers=headers, json={"symbols": symbols, "weights": {"up": 0.7, "down": -0.3}}).json()
    assert given["weights_source"] == "given" and given["exposure"]["momentum"] > 0 and given["table"]["universe"] == 3

    # An open book: weights from the open trades' notional at the supplied closes.
    from app.quant import routes as quant_routes

    class _Trade:
        def __init__(self, symbol, direction, qty):
            self.symbol, self.underlying_symbol, self.direction, self.quantity = symbol, None, direction, qty

    async def fake_open_trades(session, tenant_id, *, mode=None):
        return [_Trade("UP", "LONG", 10), _Trade("DOWN", "SHORT", 10), _Trade("OTHER", "LONG", 5)]
    monkeypatch.setattr(quant_routes, "open_trades", fake_open_trades)
    book = client.post("/api/quant/exposure", headers=headers, json={"symbols": symbols}).json()
    assert book["weights_source"] == "open book" and book["weights"]["UP"] > 0 > book["weights"]["DOWN"]
    assert abs(abs(book["weights"]["UP"]) + abs(book["weights"]["DOWN"]) - 1) < 1e-6
    assert book["exposure"]["momentum"] > 0 and any("OTHER" not in n and "ignored" in n for n in book["notes"])

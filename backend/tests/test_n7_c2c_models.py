"""N7 C2c (docs/design/C2_MODELS.md PR 3): the `span_like` margin with its margin call, `black76` pricing of strikes
missing from a recorded chain, and the option engine taking a ModelSet (flag `backtest_models` at the API).

- the defaults stay byte-identical on both engines (single-leg: tests/test_c2_models.py golden; option engine: the
  hashes below, recorded on claude/n7-c2b before this change);
- span_like refuses an entry the equity cannot margin and calls the margin at a close; on structures it uses the
  defined maximum loss, or the short legs' notional;
- black76 prices a missing strike at the nearest quoted strike's IV known by then, flags every model price, and is a
  reference Black-76 (equal to Black-Scholes on the forward);
- no look-ahead: a margin call is decided on the bar's close; a run cut anywhere agrees with the full run; an IV row
  from after the bar is never used;
- the API: the models need the flag; fill / latency on an option run and pricing on a single-leg run are 422."""
import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from app.backtest import engine
from app.backtest.models import ModelError, ModelSet, SpanLikeMargin
from app.backtest.options import Black76Pricer, OptionChainSnapshotRow, SnapshotPricer, VolatilityModel, black76
from app.backtest.options_engine import OptionBacktestConfig, run_option_backtest
from app.backtest.repro import result_hash
from app.core.enums import OptionPosition, OptionStrategy, SignalDirection
from app.core.models import RiskConfig
from app.market_data.calendar import IST
from app.option_chain.greeks import BSInputs, black_scholes
from app.option_chain.models import OptionType
from app.strategy_engine.base import BaseStrategy
from tests.test_auth_api import client
from tests.test_deployments_api import _auth
from tests.test_exit_rules_and_backtest_depth import _candles
from tests.test_n7_c2b_models import _bars
from tests.test_phase_w_option_backtest import _OneShot, _frame

RISK = RiskConfig(capital=1_000_000, risk_per_trade_pct=1.0)
OPTION_GOLDEN = {   # the option engine before C2c (claude/n7-c2b), default models
    "bull_put": "sha256:c8505a512499dbe96d1fc5f60d48aca84bfd4921d098b4fce1fe91989100a361",
    "single_buy": "sha256:5b18c602f61681c3121dcf790b6de436052075aa941da9ddfefac3dc34560c16",
    "single_write": "sha256:3dd57ca665392b83a8480a24350e4a123d7fb7447ebe894ff483ee664ad7696e",
    "butterfly": "sha256:05b3db7b2fa8e58800f13dd38133be39cd8cf667c56d50cde1a65782e2814149",
}
CASES = {
    "bull_put": (OptionBacktestConfig(option_strategy=OptionStrategy.BULL_PUT_SPREAD, implied_volatility=0.14, spread_width=2), SignalDirection.LONG),
    "single_buy": (OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=OptionPosition.BUY, implied_volatility=0.14),
                   SignalDirection.LONG),
    "single_write": (OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=OptionPosition.WRITE, implied_volatility=0.14),
                     SignalDirection.SHORT),
    "butterfly": (OptionBacktestConfig(option_strategy=OptionStrategy.LONG_BUTTERFLY, implied_volatility=0.14, spread_width=2), SignalDirection.LONG),
}


def _golden_hash(result):
    """CP6-15 bumped the engine's version label (7-options -> 8-options) and nothing else in these one-trade runs: the
    golden is compared with the label the golden was recorded under."""
    assert result.loss_streak is None
    return result_hash(result.model_copy(update={"options": {**result.options, "engine_version": "7-options"}}))


@pytest.mark.parametrize("case", sorted(CASES))
def test_option_engine_defaults_are_byte_identical(case):
    cfg, direction = CASES[case]
    assert _golden_hash(run_option_backtest(_OneShot(direction), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg)) == OPTION_GOLDEN[case]
    again = run_option_backtest(_OneShot(direction), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg, models=ModelSet())
    assert _golden_hash(again) == OPTION_GOLDEN[case] and again.execution is None


# --- span_like on the single-leg engine -----------------------------------------------------------------------------------------

class _Long(BaseStrategy):
    """LONG at the close of the bars whose index is in `at`, stop `stop` points below."""
    id = "c2c_long"
    name = "C2c long"
    timeframes = ["1min"]

    def __init__(self, at, stop=30.0):
        super().__init__()
        self.at, self.stop = set(at), stop

    def min_history(self):
        return {"1min": 1}

    def analyze(self, data, symbol):
        frame = data["1min"]
        last = frame.index[-1]
        if last not in self.at:
            return self.no_trade(symbol, last.to_pydatetime(), ["no"])
        close = float(frame["close"].iloc[-1])
        return self.build_signal(symbol, last.to_pydatetime(), SignalDirection.LONG, close, close - self.stop, 80, ["test"], target_rr=(3.0, 5.0))


SMALL = RiskConfig(capital=100_000, risk_per_trade_pct=50.0, max_trades_per_day=100, max_consecutive_losses=100, max_daily_loss_pct=90)


def _single(df, at_i, models):
    return engine.run_backtest(_Long([df.index[i] for i in at_i]), df, "TESTEQ", "1min", SMALL, models=models)


def test_span_like_refuses_what_the_equity_cannot_margin():
    df = _bars(*[np.full(20, 100.0)] * 4)
    plain = _single(df, [3], ModelSet())
    assert plain.trades and plain.execution is None
    tight = _single(df, [3], ModelSet(margin="span_like", params={"margin": {"equity_pct": 100.0}}))
    assert tight.trades == [] and tight.execution["margin_refused"] == 1 and tight.execution["margin_kind"] == "EQUITY"
    loose = _single(df, [3], ModelSet(margin="span_like", params={"margin": {"equity_pct": 5.0}}))
    assert len(loose.trades) == 1 and loose.execution["margin_refused"] == 0


def test_a_margin_call_closes_at_the_close_that_breaches():
    closes = np.r_[np.full(6, 100.0), [99.0, 98.0, 97.0], np.full(11, 97.0)]
    df = _bars(closes.copy(), closes + 0.2, closes - 0.2, closes.copy())
    qty_run = _single(df, [3], ModelSet(margin="span_like", params={"margin": {"equity_pct": 5.0}}))
    qty = qty_run.trades[0].quantity
    pct = 100.0 * 100_000 / (100.0 * qty) * 0.995           # just inside the equity at entry
    called = _single(df, [3], ModelSet(margin="span_like", params={"margin": {"equity_pct": pct}}))
    [t] = called.trades
    assert t.exit_reason == "Margin call" and t.exit_time == df.index[6] and t.exit_price == 99.0
    assert called.execution["margin_calls"] == 1
    calm = _single(df, [3], ModelSet(margin="span_like", params={"margin": {"equity_pct": 5.0}}))
    assert calm.trades[0].exit_reason != "Margin call"


@pytest.mark.parametrize("cut", [8, 13, 17])
def test_a_margin_run_cut_anywhere_agrees_with_the_full_run(cut):
    rng = np.random.default_rng(9)
    closes = 100 + np.cumsum(rng.normal(0, 1.5, 40))
    df = _bars(closes.copy(), closes + 1.0, closes - 1.0, closes.copy())
    models = ModelSet(margin="span_like", params={"margin": {"equity_pct": 40.0}})
    signals = [2, 9, 15, 22, 30]
    full = _single(df, signals, models).trades
    part = _single(df.iloc[:cut], [i for i in signals if i < cut], models).trades
    done = [t for t in full if t.exit_time < df.index[cut - 1]]
    assert [(t.entry_time, t.exit_time, t.exit_price, t.exit_reason) for t in part[:len(done)]] == \
           [(t.entry_time, t.exit_time, t.exit_price, t.exit_reason) for t in done]


def test_span_like_settings_are_checked():
    m = SpanLikeMargin()
    assert m.required("EQUITY", 1000) == 200.0 and m.required("FUTURES", 1000) == 120.0 and m.required("OPTION_LONG", 1e6) == 0.0
    assert m.required("OPTION_DEFINED", 0, max_loss=-750.0) == 750.0 and m.required("OPTION_SHORT", 1000) == 150.0
    assert m.call(99.0, 100.0) and not m.call(100.0, 100.0) and not m.call(-5.0, 0.0)
    for bad in ({"equity_pct": 0}, {"maintenance_pct": -1}):
        with pytest.raises(ModelError):
            ModelSet(margin="span_like", params={"margin": bad}).built()
    assert engine._margin_kind(None, "NIFTY24OCTFUT") == "FUTURES" and engine._margin_kind(None, "RELIANCE") == "EQUITY"


# --- the option engine: margin, pricing, refusals -------------------------------------------------------------------------------

def test_option_margin_uses_the_defined_loss_or_the_short_notional():
    cfg, _ = CASES["bull_put"]
    ok = run_option_backtest(_OneShot(), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg, models=ModelSet(margin="span_like"))
    assert ok.total_trades == 1 and ok.execution["margin_refused"] == 0              # a defined loss well inside the equity
    st = ok.options["structures"][0]
    need = st["max_loss"] * ok.trades[0].quantity
    assert 0 < need < RISK.capital
    write = OptionBacktestConfig(option_strategy=OptionStrategy.SINGLE, option_position=OptionPosition.WRITE, implied_volatility=0.14)
    sold = run_option_backtest(_OneShot(SignalDirection.SHORT), _frame(24500, 24800), "NIFTY 50", "1min", RISK, write, models=ModelSet(margin="span_like"))
    assert sold.total_trades == 1                                                      # 15 % of one lot's notional fits 10 lakh
    refused = run_option_backtest(_OneShot(SignalDirection.SHORT), _frame(24500, 24800), "NIFTY 50", "1min", RISK, write,
                                  models=ModelSet(margin="span_like", params={"margin": {"option_short_pct": 100.0}}))
    assert refused.total_trades == 0 and refused.execution["margin_refused"] == 1        # the full notional does not
    assert refused.options["signals_skipped"] == {"margin: equity below the span_like requirement": 1}


def test_option_engine_refuses_models_it_cannot_act_on():
    cfg, _ = CASES["bull_put"]
    for bad in (ModelSet(fill="next_open"), ModelSet(latency="fixed_ms"), ModelSet(slippage="volume_share"), ModelSet(pricing="black76")):
        with pytest.raises(ModelError):
            run_option_backtest(_OneShot(), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg, models=bad)


def test_black76_is_black_scholes_on_the_forward():
    s, k, t, r, sigma = 24500.0, 24600.0, 10 / 365, 0.07, 0.15
    for right, kind in (("CE", OptionType.CALL), ("PE", OptionType.PUT)):
        ref = black_scholes(BSInputs(s, k, t, r, sigma, kind)).theoretical_price
        assert black76(right, s * math.exp(r * t), k, t, r, sigma) == pytest.approx(ref, rel=1e-9)


def _rows(expiry, strikes, iv_by_minute=None):
    rows = []
    for minute in range(0, 300, 5):
        ts = datetime(2026, 10, 5, 9, 15, tzinfo=IST) + timedelta(minutes=minute)
        spot = 24500 + minute
        iv = (iv_by_minute or {}).get(minute, 14.0)
        for strike in strikes:
            rows.append(OptionChainSnapshotRow(timestamp=ts, expiry=expiry, strike=strike, right="PE", ltp=max(0.05, round(80 - (spot - strike) * 0.4, 2)), iv=iv))
            rows.append(OptionChainSnapshotRow(timestamp=ts, expiry=expiry, strike=strike, right="CE", ltp=max(0.05, round(80 + (spot - strike) * 0.4, 2)), iv=iv))
    return rows


def test_black76_prices_missing_strikes_and_flags_every_model_price():
    expiry = date(2026, 10, 6)
    rows = _rows(expiry, range(24300, 24701, 50))
    cfg = OptionBacktestConfig(option_strategy=OptionStrategy.IRON_CONDOR, strike_offset=6, spread_width=2)
    refused = run_option_backtest(_OneShot(), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg, pricer=SnapshotPricer(rows, max_age_minutes=10))
    assert refused.total_trades == 0                                   # no chain row, no fallback: refused (as before)
    priced = run_option_backtest(_OneShot(), _frame(24500, 24800), "NIFTY 50", "1min", RISK, cfg, pricer=SnapshotPricer(rows, max_age_minutes=10),
                                 models=ModelSet(pricing="black76"))
    assert priced.total_trades == 1 and priced.options["model_prices"] > 0 and priced.execution["model_prices"] == priced.options["model_prices"]
    legs = priced.options["structures"][0]["legs"]
    flagged = [leg for leg in legs if leg["model_priced"]]
    assert flagged and all(leg["strike"] > 24700 or leg["strike"] < 24300 for leg in flagged)
    assert all(f"{int(leg['strike'])} {leg['right']}" in " ".join(priced.options["model_priced_contracts"]) for leg in flagged)
    assert "Black-76" in priced.options["model_pricing"]


def test_black76_reads_only_iv_known_by_then():
    expiry = date(2026, 10, 6)
    rows = _rows(expiry, [24500], iv_by_minute={m: (14.0 if m < 100 else 60.0) for m in range(0, 300, 5)})
    p = Black76Pricer(rows, VolatilityModel(fixed_iv=0.2), max_age_minutes=10)
    early = datetime(2026, 10, 5, 9, 15, tzinfo=IST) + timedelta(minutes=50)
    late = datetime(2026, 10, 5, 9, 15, tzinfo=IST) + timedelta(minutes=150)
    assert p.implied_vol("CE", 24800, expiry, early.astimezone(timezone.utc))[0] == pytest.approx(0.14)
    assert p.implied_vol("CE", 24800, expiry, late.astimezone(timezone.utc))[0] == pytest.approx(0.60)
    before = datetime(2026, 10, 5, 9, 0, tzinfo=IST)                    # before the first row: the run's own vol model
    assert p.implied_vol("CE", 24800, expiry, before.astimezone(timezone.utc)) == (0.2, "fixed IV 20%")


# --- the API -------------------------------------------------------------------------------------------------------------------

def test_the_api_gates_and_routes_the_models():
    from tests.test_n1_e1_execution_core import _flag
    from app.backtest.models import FLAG
    headers = _auth("n7c2c-api@example.com")
    me = client.get("/api/auth/me", headers=headers).json()
    body = {"strategy_id": "ema_rsi_scalper_1m", "symbol": "nifty", "base_timeframe": "1min", "candles": _candles(), "data_source": "sample"}
    opt = {**body, "options": {"option_strategy": "BULL_PUT_SPREAD", "pricing": "synthetic", "implied_volatility": 0.14}}
    assert client.post("/api/backtest", headers=headers, json={**body, "execution_models": {"margin": "span_like"}}).status_code == 403
    assert client.post("/api/backtest", headers=headers, json={**opt, "execution_models": {"margin": "span_like"}}).status_code == 403
    _flag(FLAG, me["tenant_id"])
    single = client.post("/api/backtest", headers=headers, json={**body, "execution_models": {"margin": "span_like"}})
    assert single.status_code == 200, single.text
    assert single.json()["execution"]["models"]["margin"] == "span_like"
    option = client.post("/api/backtest", headers=headers, json={**opt, "execution_models": {"margin": "span_like"}})
    assert option.status_code == 200, option.text
    assert client.post("/api/backtest", headers=headers, json={**opt, "execution_models": {"fill": "next_open"}}).status_code == 422
    assert client.post("/api/backtest", headers=headers, json={**body, "execution_models": {"pricing": "black76"}}).status_code == 422
    synthetic_b76 = client.post("/api/backtest", headers=headers, json={**opt, "execution_models": {"pricing": "black76"}})
    assert synthetic_b76.status_code == 422 and "no chain" in synthetic_b76.text

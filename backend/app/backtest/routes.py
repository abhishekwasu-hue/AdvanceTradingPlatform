"""Phase J2: backtest records and robustness endpoints.

* `GET  /api/backtests`            - this organisation's saved runs (newest first)
* `GET  /api/backtests/{id}`       - one run with its stored metrics and analytics
* `POST /api/backtest/monte-carlo` - run the backtest, then resample its trades
* `POST /api/backtest/walk-forward`- run the same strategy on consecutive windows

`/api/backtest` itself (app/main.py) records a run whenever the caller is logged in.
"""
import copy
import json
from datetime import datetime
from typing import Dict, List, Optional

from fastapi.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.backtest import chain_recorder
from app.backtest.engine import ENGINE_VERSION, run_backtest
from app.backtest.repro import config_hash, fingerprint
from app.backtest.options import OptionChainSnapshotRow, SnapshotPricer, SyntheticPricer, VolatilityModel, underlying_name
from app.backtest.options_engine import ENGINE_VERSION as OPTIONS_ENGINE_VERSION, OptionBacktestConfig, OptionBacktestError, run_option_backtest
from app.core.enums import ExpiryRule, OptionPosition, OptionStrategy, StrikeRule
from app.instruments.spreads import MAX_CUSTOM_LEGS, MAX_LEG_RATIO, CustomLeg
from app.market_data.calendar import load_holidays
from app.backtest.optimizer import MAX_COMBOS, optimize
from app.backtest.robustness import monte_carlo, walk_forward
from app.core.models import BacktestResult, OHLCVBar, RiskConfig, bars_to_dataframe
from app.custom_strategies.resolver import resolve_strategy
from app.db.models import BacktestRunRecord, User
from app.db.session import get_session
from app.trading.exit_rules import ExitRules

router = APIRouter(tags=["backtest"])


class ExitRulesBody(BaseModel):
    trailing_stop_pct: Optional[float] = Field(default=None, gt=0, le=50)
    break_even_at_r: Optional[float] = Field(default=None, gt=0, le=10)
    time_exit_minutes: Optional[int] = Field(default=None, ge=1, le=375)
    time_exit_at: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    def to_rules(self) -> ExitRules:
        return ExitRules(**self.model_dump())


class CustomLegBody(BaseModel):
    right: str = Field(pattern="^(CE|PE|ce|pe)$")
    role: str = Field(pattern="^(SHORT|LONG|short|long)$")
    strike_rule: StrikeRule = StrikeRule.ATM
    strike_offset: int = Field(default=0, ge=0, le=20)
    ratio: int = Field(default=1, ge=1, le=MAX_LEG_RATIO)


class OptionBacktestBody(BaseModel):
    """Phase W: trade the signals as options. The deployment fields (structure, expiry/strike
    rules, width, exit percentages, custom legs, max lots) plus the simulation's own knobs."""
    option_strategy: OptionStrategy = OptionStrategy.SINGLE
    option_position: OptionPosition = OptionPosition.BUY
    expiry_rule: ExpiryRule = ExpiryRule.NEAREST
    strike_rule: StrikeRule = StrikeRule.ATM
    strike_offset: int = Field(default=0, ge=0, le=10)
    spread_width: int = Field(default=2, ge=1, le=20)
    target_credit_pct: Optional[float] = Field(default=None, ge=5, le=95)
    stop_credit_pct: Optional[float] = Field(default=None, ge=10, le=500)
    premium_stop_pct: Optional[float] = Field(default=None, ge=5, le=95)
    custom_legs: Optional[List[CustomLegBody]] = Field(default=None, max_length=MAX_CUSTOM_LEGS)
    max_lots: Optional[int] = Field(default=None, ge=1, le=500)
    # Conventions: None = the exchange's current one for the underlying.
    lot_size: Optional[int] = Field(default=None, ge=1, le=10000)
    strike_step: Optional[float] = Field(default=None, gt=0)
    expiry_weekday: Optional[int] = Field(default=None, ge=0, le=4, description="0 = Monday ... 4 = Friday")
    weekly_expiry: Optional[bool] = None
    # Pricing: "synthetic" (Black-Scholes), "snapshots" (recorded quotes, synthetic fallback when
    # allowed) or "uploaded" (the rows in `option_chain`).
    pricing: str = Field(default="synthetic", pattern=r"^(synthetic|snapshots|uploaded)$")
    implied_volatility: Optional[float] = Field(default=None, gt=0.01, le=3.0, description="fraction: 0.14 = 14%; None = realised vol")
    realised_vol_window: int = Field(default=20, ge=5, le=500)
    risk_free_rate: float = Field(default=0.07, ge=0, le=0.25)
    snapshot_max_age_minutes: int = Field(default=15, ge=1, le=1440)
    allow_synthetic_fallback: bool = True
    option_chain: Optional[List[OptionChainSnapshotRow]] = Field(default=None, max_length=500_000)
    intraday: bool = True

    def to_config(self, holidays=()) -> OptionBacktestConfig:
        return OptionBacktestConfig(
            option_strategy=self.option_strategy, option_position=self.option_position, expiry_rule=self.expiry_rule,
            strike_rule=self.strike_rule, strike_offset=self.strike_offset, spread_width=self.spread_width,
            target_credit_pct=self.target_credit_pct, stop_credit_pct=self.stop_credit_pct, premium_stop_pct=self.premium_stop_pct,
            custom_legs=[CustomLeg.from_dict(leg.model_dump()) for leg in (self.custom_legs or [])], max_lots=self.max_lots,
            lot_size=self.lot_size, strike_step=self.strike_step, expiry_weekday=self.expiry_weekday, weekly_expiry=self.weekly_expiry,
            holidays=set(holidays), implied_volatility=self.implied_volatility, realised_vol_window=self.realised_vol_window,
            risk_free_rate=self.risk_free_rate, intraday=self.intraday,
        )


class BacktestBody(BaseModel):
    strategy_id: str
    symbol: str
    base_timeframe: str
    candles: List[OHLCVBar]
    risk_config: Optional[RiskConfig] = None
    strategy_params: Optional[Dict] = None
    exit_rules: Optional[ExitRulesBody] = None
    data_source: str = Field(default="uploaded", max_length=30)
    # Phase W: present = an option backtest on the same signals.
    options: Optional[OptionBacktestBody] = None

    @property
    def engine_version(self) -> str:
        return OPTIONS_ENGINE_VERSION if self.options is not None else ENGINE_VERSION


async def _strategy(body: BacktestBody, user: Optional[User], session: AsyncSession):
    try:
        strategy = copy.copy(await resolve_strategy(body.strategy_id, user, session))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if body.strategy_params:
        strategy.params = {**strategy.params, **body.strategy_params}
    return strategy


class BacktestRunner:
    """Phase W: one object that runs `body` as an underlying or an option backtest, so every
    endpoint (plain, Monte Carlo, walk-forward) dispatches the same way. Built once per request:
    the option pricer (recorded quotes for the candle span, the holiday calendar) is loaded here."""

    def __init__(self, body: BacktestBody, risk: RiskConfig, pricer=None, holidays=()) -> None:
        self.body = body
        self.risk = risk
        self.rules = body.exit_rules.to_rules() if body.exit_rules else None
        self.config = body.options.to_config(holidays) if body.options is not None else None
        self.pricer = pricer

    @classmethod
    async def build(cls, body: BacktestBody, risk: RiskConfig, session: AsyncSession) -> "BacktestRunner":
        if body.options is None:
            return cls(body, risk)
        opts = body.options
        holidays = await load_holidays(session)
        pricer = None
        if opts.pricing != "synthetic":
            fallback = SyntheticPricer(VolatilityModel(fixed_iv=opts.implied_volatility, window=opts.realised_vol_window), opts.risk_free_rate) \
                if opts.allow_synthetic_fallback else None
            if opts.pricing == "uploaded":
                rows = opts.option_chain or []
                if not rows:
                    raise HTTPException(status_code=400, detail="pricing=uploaded needs option_chain rows")
            else:
                underlying, _ = underlying_name(body.symbol)
                start, end = _span(body.candles)
                rows = await chain_recorder.load_rows(session, underlying, start, end)
                if not rows and fallback is None:
                    raise HTTPException(status_code=400, detail=f"No recorded option-chain quotes for {underlying} in the candle span; "
                                                                 "allow the synthetic fallback or upload rows")
            pricer = SnapshotPricer(rows, max_age_minutes=opts.snapshot_max_age_minutes, fallback=fallback)
        return cls(body, risk, pricer=pricer, holidays=holidays)

    def run(self, strategy, df) -> BacktestResult:
        if self.config is None:
            result = run_backtest(strategy, df, self.body.symbol, self.body.base_timeframe, self.risk, exit_rules=self.rules)
        else:
            try:
                result = run_option_backtest(strategy, df, self.body.symbol, self.body.base_timeframe, self.risk, self.config,
                                             exit_rules=self.rules, pricer=self.pricer)
            except OptionBacktestError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Realism C4: what produced this result, as hashes (recorded with the run).
        result.reproducibility = fingerprint(
            result, df, engine_version=self.body.engine_version, strategy_id=self.body.strategy_id, params=getattr(strategy, "params", None),
            symbol=self.body.symbol.upper(), base_timeframe=self.body.base_timeframe, risk=self.risk,
            exit_rules=self.body.exit_rules, options=self.body.options.model_dump(exclude={"option_chain"}) if self.body.options else None,
            option_chain=data_version_of_chain(self.body.options.option_chain) if self.body.options and self.body.options.option_chain else None)
        return result


def data_version_of_chain(rows) -> str:
    """Uploaded option quotes are data too: hashed into the config so a different chain is a different run."""
    return config_hash(rows=[r.model_dump(mode="json") for r in rows])


def _span(candles: List[OHLCVBar]):
    from app.backtest.options import to_utc
    stamps = sorted(to_utc(c.timestamp) for c in candles)
    return stamps[0], stamps[-1]


async def record_run(session: AsyncSession, user: Optional[User], body: BacktestBody, result: BacktestResult) -> Optional[int]:
    """Persist a run for a logged-in caller: what ran, on what data, with which result."""
    if user is None or not body.candles:
        return None
    headline = result.model_dump(exclude={"trades", "equity_curve", "analytics", "options"})
    headline["analytics"] = result.analytics
    if result.options:
        # Phase W: the option run's summary without the per-structure legs (kept small on the record).
        headline["options"] = {k: v for k, v in result.options.items() if k != "structures"}
    params = dict(body.strategy_params or {})
    if body.options is not None:
        params["_options"] = body.options.model_dump(exclude={"option_chain"}, exclude_none=True)
    record = BacktestRunRecord(
        tenant_id=user.tenant_id, user_id=user.id, strategy_id=body.strategy_id, symbol=body.symbol.upper(),
        base_timeframe=body.base_timeframe, params_json=json.dumps(params, default=str) if params else None,
        exit_rules=result.exit_rules, data_source=body.data_source, bars=len(body.candles),
        data_from=body.candles[0].timestamp, data_to=body.candles[-1].timestamp, engine_version=body.engine_version,
        metrics_json=json.dumps(headline, default=str),
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record.id


def _run_summary(r: BacktestRunRecord, full: bool = False) -> dict:
    metrics = json.loads(r.metrics_json or "{}")
    out = {
        "id": r.id, "strategy_id": r.strategy_id, "symbol": r.symbol, "base_timeframe": r.base_timeframe,
        "params": json.loads(r.params_json) if r.params_json else None, "exit_rules": json.loads(r.exit_rules) if r.exit_rules else None,
        "data_source": r.data_source, "bars": r.bars, "data_from": r.data_from.isoformat() if r.data_from else None,
        "data_to": r.data_to.isoformat() if r.data_to else None, "engine_version": r.engine_version,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "total_trades": metrics.get("total_trades"), "net_pnl": metrics.get("net_pnl"), "win_rate": metrics.get("win_rate"),
        "max_drawdown": metrics.get("max_drawdown"), "profit_factor": metrics.get("profit_factor"),
        "reproducibility": metrics.get("reproducibility"),
    }
    if full:
        out["metrics"] = metrics
    return out


@router.get("/api/backtests")
async def list_runs(limit: int = 50, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(BacktestRunRecord).where(BacktestRunRecord.tenant_id == user.tenant_id)
                                 .order_by(BacktestRunRecord.id.desc()).limit(max(1, min(limit, 500))))
    return [_run_summary(r) for r in rows]


@router.get("/api/backtests/{run_id}")
async def get_run(run_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    record = await session.get(BacktestRunRecord, run_id)
    if record is None or record.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such backtest run")
    return _run_summary(record, full=True)


@router.post("/api/backtest/monte-carlo")
async def backtest_monte_carlo(
    body: BacktestBody, runs: int = 1000, seed: Optional[int] = 42,
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> dict:
    strategy = await _strategy(body, user, session)
    risk = body.risk_config or RiskConfig()
    runner = await BacktestRunner.build(body, risk, session)
    result = await run_in_threadpool(runner.run, strategy, bars_to_dataframe(body.candles))        # P0.1 / S3
    mc = await run_in_threadpool(monte_carlo, result.trades, risk.capital, runs=max(100, min(runs, 5000)), seed=seed)
    return {"backtest": result.model_dump(exclude={"trades", "equity_curve", "analytics", "options"}), "monte_carlo": mc}


@router.post("/api/backtest/walk-forward")
async def backtest_walk_forward(
    body: BacktestBody, folds: int = 4,
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session),
) -> dict:
    strategy = await _strategy(body, user, session)
    risk = body.risk_config or RiskConfig()
    runner = await BacktestRunner.build(body, risk, session)
    return await run_in_threadpool(walk_forward, strategy, bars_to_dataframe(body.candles), body.symbol, body.base_timeframe, risk, folds=folds,
                                   runner=runner.run if body.options is not None else None)


# --- Phase W: recorded option-chain quotes -------------------------------------------------

@router.get("/api/backtest/option-chain/coverage")
async def option_chain_coverage(underlying: Optional[str] = None, user: User = Depends(get_current_user),
                                session: AsyncSession = Depends(get_session)) -> List[dict]:
    """What the recorder has captured so far, per underlying (platform-wide reference data)."""
    return await chain_recorder.coverage(session, underlying_name(underlying)[0] if underlying else None)


@router.get("/api/backtest/option-chain/snapshots")
async def option_chain_snapshots(underlying: str, start: datetime, end: datetime, limit: int = 20000,
                                 user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await chain_recorder.load_rows(session, underlying_name(underlying)[0], start, end, limit=max(1, min(limit, 200_000)))
    return [r.model_dump(mode="json") for r in rows]


class SnapshotUploadBody(BaseModel):
    underlying: str = Field(min_length=1, max_length=50)
    rows: List[OptionChainSnapshotRow] = Field(min_length=1, max_length=200_000)


@router.post("/api/backtest/option-chain/snapshots")
async def upload_option_chain_snapshots(body: SnapshotUploadBody, user: User = Depends(get_current_user),
                                        session: AsyncSession = Depends(get_session)) -> dict:
    """Bring your own quotes: rows exported from another recorder become platform history."""
    underlying, _ = underlying_name(body.underlying)
    written = await chain_recorder.store_uploaded(session, underlying, body.rows, source=f"upload:{user.tenant_id}")
    return {"underlying": underlying, "received": len(body.rows), "written": written}


class OptimizeBody(BaseModel):
    strategy_id: str
    symbol: str
    base_timeframe: str
    candles: List[OHLCVBar]
    param_grid: Dict[str, List] = Field(description=f"parameter -> candidate values; at most {MAX_COMBOS} combinations")
    metric: str = Field(default="net_pnl", pattern=r"^(net_pnl|expectancy|profit_factor|win_rate)$")
    split: float = Field(default=0.7, ge=0.5, le=0.9)
    risk_config: Optional[RiskConfig] = None
    # Trade port (data_policy): bars from here on are the sealed holdout - dropped before the search (default: the
    # operator's BACKTEST_HOLDOUT_START, unset = none).
    holdout_start: Optional[datetime] = None


@router.post("/api/backtest/optimize")
async def backtest_optimize(body: OptimizeBody, user: User = Depends(get_current_user),
                            session: AsyncSession = Depends(get_session)) -> dict:
    """Phase M / V4.6: grid search on the in-sample part; P0.6 / B3: ranked in-sample, `validation` is the out-of-sample figure."""
    from app.platform.controls import require_flag
    await require_flag(session, "backtest_optimizer", user.tenant_id)  # Phase N4
    strategy = await _strategy(BacktestBody(strategy_id=body.strategy_id, symbol=body.symbol, base_timeframe=body.base_timeframe,
                                            candles=body.candles, risk_config=body.risk_config), user, session)
    try:
        return await run_in_threadpool(optimize, strategy, bars_to_dataframe(body.candles), body.symbol, body.base_timeframe,
                                       body.risk_config or RiskConfig(), body.param_grid, metric=body.metric, split=body.split,
                                       holdout_start=body.holdout_start)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

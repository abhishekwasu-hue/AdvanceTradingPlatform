"""Phase Z: quant endpoints (V4.6-4.8).

* `POST /api/quant/factors`  - a universe of symbols (candles, optional fundamentals) -> factor table
* `POST /api/quant/risk`     - candles (+ optional weights, benchmark) -> correlation, betas, vols,
                               portfolio risk, inverse-vol and risk-parity suggestions
* `POST /api/quant/exposure` - (login) the tenant's open book's factor tilt: weights from the open
                               trades' notional at the supplied closes, or explicit weights

Pure functions of their input like the scanner and the backtester; no persistence. Phase AG: when
a symbol comes without `fundamentals`, `/factors` and `/exposure` derive `pe, pb, roe_pct,
debt_to_equity, earnings_growth_pct` from the Fundamentals module's stored company financials
(`use_fundamentals`, default on), so the value and quality factors exist for covered symbols.
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.models import OHLCVBar, bars_to_dataframe
from app.db.models import User
from app.db.session import get_session
from app.portfolio.engine import open_trades
from app.quant import factors as factor_model, fundamentals_bridge, risk as risk_model

router = APIRouter(prefix="/api/quant", tags=["quant"])


class QuantSymbol(BaseModel):
    symbol: str = Field(min_length=1, max_length=50)
    candles: List[OHLCVBar] = Field(max_length=5000)
    fundamentals: Optional[Dict[str, float]] = Field(default=None, description="pe, pb, roe_pct, debt_to_equity, earnings_growth_pct")


class FactorsBody(BaseModel):
    symbols: List[QuantSymbol] = Field(min_length=1, max_length=200)
    weights: Optional[Dict[str, float]] = None
    momentum_lookback: int = Field(default=120, ge=5, le=2000)
    skip_recent: int = Field(default=5, ge=0, le=100)
    reversal_lookback: int = Field(default=5, ge=1, le=100)
    vol_lookback: int = Field(default=60, ge=5, le=2000)
    liquidity_lookback: int = Field(default=20, ge=1, le=500)
    use_fundamentals: bool = Field(default=True, description="fill missing fundamentals from the Fundamentals module's stored financials")


class RiskBody(BaseModel):
    symbols: List[QuantSymbol] = Field(min_length=1, max_length=200)
    weights: Optional[Dict[str, float]] = None
    benchmark: Optional[str] = None
    lookback: int = Field(default=250, ge=20, le=5000)


class ExposureBody(BaseModel):
    symbols: List[QuantSymbol] = Field(min_length=1, max_length=200)
    weights: Optional[Dict[str, float]] = Field(default=None, description="signed weights; omitted = the open book's notional weights")
    factor_weights: Optional[Dict[str, float]] = None
    mode: Optional[str] = Field(default=None, pattern="^(PAPER|LIVE)$")
    use_fundamentals: bool = True


def _inputs(symbols: List[QuantSymbol]) -> List[factor_model.FactorInputs]:
    out = []
    for s in symbols:
        df = bars_to_dataframe(s.candles) if s.candles else None
        out.append(factor_model.FactorInputs(symbol=s.symbol.upper().strip(), df=df, fundamentals=s.fundamentals))
    return out


async def _fill_fundamentals(session: AsyncSession, inputs: List[factor_model.FactorInputs], enabled: bool) -> dict:
    """Phase AG: derive the value/quality inputs from stored financials for symbols the caller left without fundamentals."""
    if not enabled:
        return {"enabled": False, "filled": {}, "note": "fundamentals fill disabled by the caller"}
    wanted = [i for i in inputs if not i.fundamentals]
    if not wanted:
        return {"enabled": True, "filled": {}, "note": "every symbol came with its own fundamentals"}
    closes = {i.symbol: float(i.df["close"].iloc[-1]) for i in wanted if i.df is not None and not i.df.empty}
    fills = await fundamentals_bridge.fundamentals_for(session, [i.symbol for i in wanted], closes)
    for i in wanted:
        fill = fills.get(i.symbol)
        if fill is not None and fill.values:
            i.fundamentals = dict(fill.values)
    covered = sum(1 for f in fills.values() if f.values)
    uncovered = [i.symbol for i in wanted if i.symbol not in fills]
    note = f"value/quality inputs derived from stored financials for {covered} of {len(wanted)} symbol(s) without caller-supplied fundamentals"
    if uncovered:
        note += f"; no company profile for {', '.join(uncovered[:10])}{'...' if len(uncovered) > 10 else ''} (add one under Fundamentals)"
    return {"enabled": True, "filled": {k: v.as_dict() for k, v in fills.items()}, "note": note}


@router.post("/factors")
async def factors(body: FactorsBody, session: AsyncSession = Depends(get_session)) -> dict:
    inputs = _inputs(body.symbols)
    fundamentals = await _fill_fundamentals(session, inputs, body.use_fundamentals)
    try:
        table = factor_model.score_universe(inputs, body.weights, momentum_lookback=body.momentum_lookback, skip_recent=body.skip_recent,
                                            reversal_lookback=body.reversal_lookback, vol_lookback=body.vol_lookback, liquidity_lookback=body.liquidity_lookback)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out = table.as_dict()
    out["fundamentals"] = fundamentals
    return out


@router.post("/risk")
async def risk(body: RiskBody) -> dict:
    frames = {s.symbol.upper().strip(): bars_to_dataframe(s.candles) for s in body.symbols if s.candles}
    if not frames:
        raise HTTPException(status_code=400, detail="No candles supplied")
    bpy = max(factor_model.bars_per_year_of(df) for df in frames.values())
    try:
        return risk_model.summarise(frames, weights={k.upper(): v for k, v in (body.weights or {}).items()} or None,
                                    benchmark=body.benchmark.upper().strip() if body.benchmark else None, lookback=body.lookback, bars_per_year=bpy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/exposure")
async def exposure(body: ExposureBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    inputs = _inputs(body.symbols)
    fundamentals = await _fill_fundamentals(session, inputs, body.use_fundamentals)
    try:
        table = factor_model.score_universe(inputs, body.factor_weights)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    notes: List[str] = []
    if body.weights:
        weights = {k.upper().strip(): float(v) for k, v in body.weights.items()}
        source = "given"
    else:
        closes = {i.symbol: float(i.df["close"].iloc[-1]) for i in inputs if i.df is not None and not i.df.empty}
        notional: Dict[str, float] = {}
        skipped = 0
        for trade in await open_trades(session, user.tenant_id, mode=body.mode):
            symbol = (trade.underlying_symbol or trade.symbol or "").upper()
            price = closes.get(symbol)
            if price is None:
                skipped += 1
                continue
            sign = 1.0 if str(trade.direction).upper().endswith("LONG") else -1.0
            notional[symbol] = notional.get(symbol, 0.0) + sign * price * float(trade.quantity or 0.0)
        gross = sum(abs(v) for v in notional.values())
        weights = {k: round(v / gross, 6) for k, v in notional.items()} if gross > 0 else {}
        source = "open book"
        if skipped:
            notes.append(f"{skipped} open position(s) on symbols not in the supplied universe were ignored")
        if not weights:
            notes.append("no open positions on the supplied symbols - exposure is empty")
    return {"weights": weights, "weights_source": source, "exposure": factor_model.exposure(weights, table), "table": table.as_dict(), "notes": notes, "fundamentals": fundamentals,
            "reading": "A tilt above +0.5 or below -0.5 on a factor means the book leans hard on it; a book that is all one bucket carries one bet."}

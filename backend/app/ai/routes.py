"""Phase L: `/api/ai/*` - provider settings, the strategy generator with its review gate, the
regime classifier and the monitoring agent's action queue. Every mutating call is a human's."""
import copy
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import generator, interview, monitor, settings as ai_settings
from app.ai.providers import ProviderError
from app.ai.regime import REGIMES, classify_regime
from app.auth.dependencies import get_current_user, require_owner, require_trader
from app.backtest.engine import ENGINE_VERSION, run_backtest
from app.backtest.routes import _run_summary
from app.core.models import OHLCVBar, RiskConfig, bars_to_dataframe
from app.db.models import AiActionRecord, AiStrategyDraftRecord, BacktestRunRecord, Tenant, User
from app.db.session import get_session
from app.plans.limits import require_feature
from app.risk_engine.routes import get_tenant_risk_config
from app.strategy_engine.declarative import DeclarativeStrategy
from app.trading.exit_rules import ExitRules
from app.platform.controls import require_flag

router = APIRouter(prefix="/api/ai", tags=["ai"])


class ProviderBody(BaseModel):
    provider: str = Field(pattern=r"^(anthropic|openai|rule_based)$")
    model: Optional[str] = Field(default=None, max_length=80)
    api_key: Optional[str] = Field(default=None, min_length=8, max_length=300)
    enabled: bool = True


class GenerateBody(BaseModel):
    prompt: str = Field(min_length=10, max_length=4000)
    # Phase V3: the reply language, the regime the page read and the symbol the idea is about.
    language: str = Field(default="en", min_length=2, max_length=5, pattern=r"^[A-Za-z-]+$")
    regime: Optional[str] = Field(default=None, max_length=20)
    symbol: Optional[str] = Field(default=None, max_length=50)


class DraftBacktestBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=50)
    base_timeframe: str = Field(default="1min", max_length=10)
    candles: List[OHLCVBar] = Field(min_length=30)
    risk_config: Optional[RiskConfig] = None
    data_source: str = Field(default="uploaded", max_length=30)


class ApproveBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    accept_risk: bool = Field(default=False, description="Phase V2: the human confirms the 'user must accept' statement")


class NoteBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=300)


class RegimeBody(BaseModel):
    candles: List[OHLCVBar] = Field(min_length=10)


async def _tenant(session: AsyncSession, user: User) -> Tenant:
    return await session.get(Tenant, user.tenant_id)


# --- L1 provider settings ------------------------------------------------------------------------

@router.get("/provider")
async def get_provider(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    return ai_settings.as_dict(await ai_settings.get_config(session, user.tenant_id), await _tenant(session, user))


@router.put("/provider")
async def put_provider(body: ProviderBody, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> dict:
    tenant = await _tenant(session, user)
    if body.provider != "rule_based":
        require_feature(tenant, "ai_features", "External AI providers")
    try:
        record = await ai_settings.save_config(session, user, provider=body.provider, model=body.model, api_key=body.api_key, enabled=body.enabled)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ai_settings.as_dict(record, tenant)


@router.delete("/provider", status_code=204)
async def delete_provider(user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> None:
    await ai_settings.delete_config(session, user)


# --- L2 generator + review gate ------------------------------------------------------------------

@router.post("/drafts", status_code=201)
async def generate_draft(body: GenerateBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    await require_flag(session, "ai_copilot", user.tenant_id)  # Phase N4 operator kill flag
    tenant = await _tenant(session, user)
    regime = body.regime.upper() if body.regime and body.regime.upper() in REGIMES else None
    draft = await generator.generate(session, tenant, user, body.prompt, regime=regime, language=body.language, symbol=body.symbol)
    return generator.as_dict(draft)


@router.get("/context")
async def runtime_context(language: str = Query(default="en", max_length=5), regime: Optional[str] = Query(default=None, max_length=20),
                          symbol: Optional[str] = Query(default=None, max_length=50),
                          user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase V3: what the AI will be told about this account right now (transparency), and the
    prompt version it will answer under."""
    from app.ai.prompt import PROMPT_VERSION, build_runtime_context
    tenant = await _tenant(session, user)
    cfg, ceilings = await generator._effective_risk(session, tenant.id)
    context = await build_runtime_context(session, tenant, user, cfg, ceilings, regime=(regime or "").upper() or None, language=language, symbol=symbol)
    return {"prompt_version": PROMPT_VERSION, "context": context.as_dict()}


@router.get("/drafts")
async def list_drafts(limit: int = Query(default=50, ge=1, le=200), user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)) -> List[dict]:
    rows = await session.scalars(select(AiStrategyDraftRecord).where(AiStrategyDraftRecord.tenant_id == user.tenant_id)
                                 .order_by(AiStrategyDraftRecord.id.desc()).limit(limit))
    return [generator.as_dict(d) for d in rows]


async def _draft(session: AsyncSession, draft_id: int, user: User) -> AiStrategyDraftRecord:
    draft = await session.get(AiStrategyDraftRecord, draft_id)
    if draft is None or draft.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such draft")
    return draft


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: int, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    return generator.as_dict(await _draft(session, draft_id, user), include_raw=True)


@router.post("/drafts/{draft_id}/backtest")
async def backtest_draft(draft_id: int, body: DraftBacktestBody, user: User = Depends(require_trader),
                         session: AsyncSession = Depends(get_session)) -> dict:
    """Runs the draft through the ordinary engine and records the run against the draft - the
    evidence the approval step requires."""
    import json
    draft = await _draft(session, draft_id, user)
    try:
        config = generator.draft_config(draft)
    except generator.GenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    strategy = DeclarativeStrategy(f"ai_draft_{draft.id}", config)
    risk = body.risk_config or await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
    result = run_backtest(copy.copy(strategy), bars_to_dataframe(body.candles), body.symbol.upper(), body.base_timeframe, risk)
    headline = {k: getattr(result, k, None) for k in ("total_trades", "win_rate", "net_pnl", "max_drawdown", "profit_factor", "expectancy", "gross_pnl", "total_charges")}
    headline["analytics"] = result.analytics
    run = BacktestRunRecord(tenant_id=user.tenant_id, user_id=user.id, strategy_id=f"ai_draft_{draft.id}", symbol=body.symbol.upper(),
                            base_timeframe=body.base_timeframe, params_json=None, exit_rules=result.exit_rules, data_source=body.data_source,
                            bars=len(body.candles), data_from=body.candles[0].timestamp, data_to=body.candles[-1].timestamp,
                            engine_version=ENGINE_VERSION, metrics_json=json.dumps(headline, default=str))
    session.add(run)
    await session.flush()
    try:
        await generator.attach_backtest(session, draft, run)
    except generator.GenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"draft": generator.as_dict(draft), "run": _run_summary(run), "result": result.model_dump()}


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(draft_id: int, body: ApproveBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    draft = await _draft(session, draft_id, user)
    tenant = await _tenant(session, user)
    try:
        record = await generator.approve(session, draft, user, tenant, name=body.name, accept_risk=body.accept_risk)
    except generator.GenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"draft": generator.as_dict(draft), "custom_strategy_id": record.id, "strategy_id": f"custom_{record.id}", "origin": record.origin}


@router.post("/drafts/{draft_id}/reject")
async def reject_draft(draft_id: int, body: NoteBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    draft = await _draft(session, draft_id, user)
    try:
        return generator.as_dict(await generator.reject(session, draft, user, body.note))
    except generator.GenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Phase AP strategy interview --------------------------------------------------------------------

class InterviewStartBody(BaseModel):
    prompt: str = Field(default="", max_length=4000)


class InterviewPlanBody(BaseModel):
    answers: interview.InterviewAnswers
    base_timeframe: str = Field(default="5min", max_length=10)
    candles: List[OHLCVBar] = Field(min_length=interview.MIN_BARS, max_length=20_000)
    data_source: str = Field(default="sample", max_length=30)


@router.get("/interview/questions")
async def interview_questions(user: User = Depends(get_current_user)) -> dict:
    return {"questions": [q.as_dict() for q in interview.QUESTIONS]}


@router.post("/interview/start")
async def interview_start(body: InterviewStartBody, user: User = Depends(get_current_user)) -> dict:
    """Is the request vague (interview first) or a rule description (generate directly)? Plus the
    answers the text already gives, so they are not asked again."""
    return interview.start(body.prompt)


@router.post("/interview/plan")
async def interview_plan(body: InterviewPlanBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """The professional's plan for this trader: market read, strategy choice with evidence, risk
    settings, capital allocation, R:R, contract and a PAPER deployment - shown, never applied."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    from app.platform.controls import risk_ceilings
    if interview.timeframe_minutes(body.base_timeframe) is None or body.base_timeframe == "day":
        raise HTTPException(status_code=400, detail=f"Use intraday candles for the interview, not {body.base_timeframe!r}")
    df = interview.frame_from_candles(body.candles[-interview.MAX_BARS:])
    ceilings = await risk_ceilings(session)
    # The evidence step walks strategies bar by bar (seconds of CPU): off the event loop.
    return await run_in_threadpool(interview.build_plan, body.answers, df, body.base_timeframe, ceilings=ceilings, data_source=body.data_source)


# --- L3 regime -------------------------------------------------------------------------------------

@router.get("/regimes")
async def regimes() -> dict:
    return {"regimes": list(REGIMES), "note": "Set regime_filter on a deployment to enter only in the listed regimes; empty = any."}


@router.post("/regime")
async def regime(body: RegimeBody, user: User = Depends(get_current_user)) -> dict:
    return classify_regime(bars_to_dataframe(body.candles)).to_dict()


# --- L4 monitoring agent -----------------------------------------------------------------------------

@router.get("/actions")
async def list_actions(status: Optional[str] = Query(default=None, max_length=10), limit: int = Query(default=100, ge=1, le=500),
                       user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> List[dict]:
    query = select(AiActionRecord).where(AiActionRecord.tenant_id == user.tenant_id)
    if status:
        query = query.where(AiActionRecord.status == status.upper())
    rows = await session.scalars(query.order_by(AiActionRecord.id.desc()).limit(limit))
    return [monitor.as_dict(r) for r in rows]


async def _action(session: AsyncSession, action_id: int, user: User) -> AiActionRecord:
    row = await session.get(AiActionRecord, action_id)
    if row is None or row.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="No such proposal")
    return row


@router.post("/actions/{action_id}/approve")
async def approve_action(action_id: int, body: NoteBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    """Approve and execute in one human step (pause / acknowledge). Exits need a live price, so
    an EXIT_POSITION approval executes through the worker's price source when available and
    otherwise tells the user to close from the Positions page."""
    row = await _action(session, action_id, user)
    try:
        row = await monitor.decide(session, row, user, approve=True, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    price_lookup = None
    if row.action == "EXIT_POSITION":
        from app.ai.worker_bridge import price_lookup_for_tenant
        price_lookup = await price_lookup_for_tenant(session, user.tenant_id)
    row = await monitor.execute(session, row, user, price_lookup=price_lookup)
    return monitor.as_dict(row)


@router.post("/actions/{action_id}/reject")
async def reject_action(action_id: int, body: NoteBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    row = await _action(session, action_id, user)
    try:
        return monitor.as_dict(await monitor.decide(session, row, user, approve=False, note=body.note))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

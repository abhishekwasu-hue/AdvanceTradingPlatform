"""Phase L: `/api/ai/*` - provider settings, the strategy generator with its review gate, the
regime classifier and the monitoring agent's action queue. Every mutating call is a human's."""
import copy
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import advisor, briefing, coach, copilot, generator, market_study, strategist, global_cues, interview, knowledge, market_memory, monitor, settings as ai_settings
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
    # Phase AQ: None = the preferences saved in the trader's profile.
    preferences: Optional[advisor.Preferences] = None


class InterviewRefineBody(InterviewPlanBody):
    feedback: List[str] = Field(min_length=1, max_length=len(advisor.FEEDBACK_CODES))
    option_id: Optional[str] = Field(default=None, max_length=20)
    strategy_id: Optional[str] = Field(default=None, max_length=100)


class InterviewChooseBody(BaseModel):
    answers: interview.InterviewAnswers
    option_id: str = Field(max_length=20)
    strategy_id: Optional[str] = Field(default=None, max_length=100)
    match: int = Field(default=0, ge=0, le=100)


@router.get("/interview/questions")
async def interview_questions(user: User = Depends(get_current_user)) -> dict:
    return {"questions": [q.as_dict() for q in interview.QUESTIONS], "feedback_options": advisor.feedback_options()}


@router.post("/interview/start")
async def interview_start(body: InterviewStartBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Is the request vague (interview first) or a rule description (generate directly)? Plus the
    answers the text already gives, so they are not asked again, and the saved profile (Phase AQ)."""
    out = interview.start(body.prompt)
    saved, prefs = await advisor.load_profile(session, user)
    out["profile"] = None if saved is None else {"answers": saved, "preferences": prefs.model_dump()}
    return out


def _df_for(body: InterviewPlanBody):
    if interview.timeframe_minutes(body.base_timeframe) is None or body.base_timeframe == "day":
        raise HTTPException(status_code=400, detail=f"Use intraday candles for the interview, not {body.base_timeframe!r}")
    return interview.frame_from_candles(body.candles[-interview.MAX_BARS:])


async def _options(session: AsyncSession, user: User, answers: interview.InterviewAnswers, prefs: advisor.Preferences,
                   body: InterviewPlanBody) -> dict:
    from app.platform.controls import risk_ceilings
    df = _df_for(body)
    ceilings = await risk_ceilings(session)
    memory = await market_memory.latest(session, user.tenant_id)   # Phase AR: background for the plan
    # The evidence step walks strategies bar by bar (seconds of CPU): off the event loop.
    result = await run_in_threadpool(advisor.build_options, answers, prefs, df, body.base_timeframe, ceilings=ceilings,
                                     data_source=body.data_source, memory=memory)
    await advisor.save_profile(session, user, answers, advisor.Preferences.model_validate(result["preferences"]))
    return result


@router.post("/interview/plan")
async def interview_plan(body: InterviewPlanBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """The professional's plan as three options (safe / balanced / active) with a match % each:
    market read, strategy with evidence, risk settings, capital allocation, R:R, contract and a
    PAPER deployment - shown, never applied. The answers are remembered in the trader profile."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    _df_for(body)
    prefs = body.preferences
    if prefs is None:
        _, prefs = await advisor.load_profile(session, user)
    # A new plan starts a new conversation: the match history counts this conversation's rounds.
    prefs = prefs.model_copy(update={"match_history": []})
    return await _options(session, user, body.answers, prefs, body)


@router.post("/interview/refine")
async def interview_refine(body: InterviewRefineBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """"Not this one, because..." - the reasons become preferences and the options are rebuilt."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    bad = [c for c in body.feedback if c not in advisor.FEEDBACK_CODES]
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown feedback {bad}; valid: {list(advisor.FEEDBACK_CODES)}")
    _df_for(body)
    prefs = body.preferences
    if prefs is None:
        _, prefs = await advisor.load_profile(session, user)
    answers, prefs, changes = advisor.apply_feedback(body.answers, prefs, body.feedback, body.strategy_id, body.option_id)
    result = await _options(session, user, answers, prefs, body)
    result["changes"] = changes
    return result


@router.post("/interview/choose")
async def interview_choose(body: InterviewChooseBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Remembers the option the trader picked; the next round leans towards it."""
    _, prefs = await advisor.load_profile(session, user)
    prefs.chosen = (prefs.chosen + [{"at": datetime.now(timezone.utc).isoformat(), "option": body.option_id,
                                     "strategy_id": body.strategy_id, "match": body.match}])[-20:]
    await advisor.save_profile(session, user, body.answers, prefs)
    return {"preferences": prefs.model_dump()}


@router.get("/profile")
async def get_profile(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    saved, prefs = await advisor.load_profile(session, user)
    return {"answers": saved, "preferences": prefs.model_dump()}


@router.delete("/profile", status_code=204)
async def delete_profile(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    """Forget everything the Copilot learnt about this trader."""
    await advisor.delete_profile(session, user)


# --- Phase AR market memory ----------------------------------------------------------------------------

class MemoryRefreshBody(BaseModel):
    symbols: Optional[List[str]] = Field(default=None, max_length=market_memory.MAX_WATCH)
    broker: Optional[str] = Field(default=None, max_length=20)
    language: str = Field(default="mr", pattern=r"^(en|mr)$")


def _with_global(out: dict, lang: str) -> dict:
    from app.ai import sentiment
    out["sentiment_view"] = sentiment.view(lang, out.get("sentiment"))     # Phase BC
    out["global_view"] = global_cues.view(lang, out.get("globals", []))
    out["global_source"] = global_cues.SOURCE_NOTE_MR if lang == "mr" else global_cues.SOURCE_NOTE_EN
    out["global_gift_note"] = global_cues.GIFT_NOTE_MR if lang == "mr" else global_cues.GIFT_NOTE_EN
    out["global_enabled"] = global_cues.enabled()
    return out


@router.get("/market-memory")
async def get_market_memory(symbol: Optional[str] = Query(default=None, max_length=50), language: str = Query(default="mr", pattern=r"^(en|mr)$"),
                            user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """The newest market read per watched symbol, the market cues (India VIX, index day change), the
    global cues with what they usually mean for India, and each symbol's bias over the last sessions
    - captured by the worker every 15 minutes."""
    out = await market_memory.latest(session, user.tenant_id, symbol=symbol)
    out["watchlist"] = await market_memory.watchlist(session, user.tenant_id)
    out["interval_minutes"] = market_memory.INTERVAL_MINUTES
    return _with_global(out, language)


@router.post("/market-memory/refresh")
async def refresh_market_memory(body: MemoryRefreshBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Reads the market now through this tenant's broker session (the worker does the same on its own),
    and the global cues. Without a broker session the global cues alone are read; with neither, 409."""
    from app.brokers.token_lifecycle import build_adapter
    from app.market_data.candles_routes import _pick_record
    from app.market_data.service import MarketDataService
    try:
        record = await _pick_record(session, user.tenant_id, body.broker, "primary")
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        report = await market_memory.capture_global(session, user.tenant_id)
        if not report["globals"]:
            raise
        report = {"symbols": 0, "cues": 0, **report, "errors": [f"broker: {exc.detail}"] + report["errors"]}
    else:
        symbols = [s.strip().upper() for s in body.symbols] if body.symbols else None
        adapter = build_adapter(record)
        report = await market_memory.capture(session, user.tenant_id, MarketDataService, adapter, symbols=symbols)
        try:                                                                 # Phase BC: sentiment on the same read
            from app.ai import sentiment
            from app.news_feed import service as news_feed_service
            memory = await market_memory.latest(session, user.tenant_id)
            news_items = await news_feed_service.items(session, user.tenant_id, hours=sentiment.NEWS_HOURS) if await news_feed_service.enabled(session, user.tenant_id) else []
            await sentiment.capture(session, user.tenant_id, adapter, memory, news_items=news_items)
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            report["errors"] = list(report.get("errors", [])) + [f"sentiment: {type(exc).__name__}: {exc}"[:200]]
    out = await market_memory.latest(session, user.tenant_id)
    out["report"] = report
    return _with_global(out, body.language)


# --- Phase AT the guide ------------------------------------------------------------------------------------

class AskBody(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    language: Optional[str] = Field(default=None, pattern=r"^(en|mr)$")


@router.get("/concepts")
async def concepts(language: str = Query(default="mr", pattern=r"^(en|mr)$"), user: User = Depends(get_current_user)) -> dict:
    """The guide's concept library (titles), for browsing."""
    return {"concepts": knowledge.catalogue(language)}


@router.get("/concepts/{concept_id}")
async def concept(concept_id: str, language: str = Query(default="mr", pattern=r"^(en|mr)$"), user: User = Depends(get_current_user)) -> dict:
    found = knowledge.BY_ID.get(concept_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such concept")
    return found.as_dict(language)


@router.post("/ask")
async def ask(body: AskBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Ask the guide: answered from the concept library and the market memory, or - with an external
    AI provider configured - by the AI grounded on the same notes, the memory and the trader's profile."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    lang = body.language or interview.detect_language(body.question)
    memory = await market_memory.latest(session, user.tenant_id)
    saved, _ = await advisor.load_profile(session, user)
    provider = await ai_settings.provider_for(session, await _tenant(session, user))
    if provider.name == "rule_based":
        return knowledge.answer(body.question, lang, memory)
    result = await knowledge.ai_answer(provider, body.question, lang, memory, saved)
    await ai_settings.mark_used(session, user.tenant_id, error=result.get("note"))
    await session.commit()
    return result


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


# --- Phase AV the Copilot home: briefing, coach, ask-anything ----------------------------------------------

async def _coach_review(session: AsyncSession, user: User, lang: str, days: int, mode: str) -> dict:
    from datetime import timedelta
    from app.db.models import TradeRecord
    since = datetime.now(timezone.utc) - timedelta(days=days)
    query = select(TradeRecord).where(TradeRecord.tenant_id == user.tenant_id, TradeRecord.user_id == user.id,
                                      TradeRecord.exit_time.isnot(None), TradeRecord.exit_time >= since)
    if mode != "ALL":
        query = query.where(TradeRecord.mode == mode)
    trades = list(await session.scalars(query.order_by(TradeRecord.exit_time).limit(5000)))
    cfg = await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
    return coach.review(trades, lang, cfg, days=days, mode=mode)


@router.get("/brief")
async def daily_brief(language: str = Query(default="mr", pattern=r"^(en|mr)$"), user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)) -> dict:
    """Today's briefing: the day type and game plan, the session, your P&L and risk budget, why each
    deployment is or is not trading, and the pre-trade checklist."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    return await briefing.build(session, user, language)


@router.get("/coach")
async def trade_coach(language: str = Query(default="mr", pattern=r"^(en|mr)$"), days: int = Query(default=30, ge=1, le=365),
                      mode: str = Query(default="ALL", pattern=r"^(ALL|PAPER|LIVE)$"), user: User = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)) -> dict:
    """The trade coach: your closed trades' numbers, breakdowns and behaviour flags with fixes."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    return await _coach_review(session, user, language, days, mode)


class CopilotBody(BaseModel):
    message: str = Field(min_length=2, max_length=1000)
    language: Optional[str] = Field(default=None, pattern=r"^(en|mr)$")


async def copilot_answer(session: AsyncSession, user: User, message: str, lang: Optional[str] = None) -> dict:
    """The Copilot router as a function (the web box and the Telegram chat share it): routes the
    message to the briefing, the coach, the deployments, the strategy interview or the guide, and
    answers - by the AI grounded on those facts when an external provider is configured, from the
    rules otherwise. Read-only."""
    lang = lang or interview.detect_language(message)
    name = copilot.intent(message)
    out: dict = {"intent": name, "language": lang, "action": copilot.action_for(lang, name), "source": "rules"}
    if name == "guide":
        memory = await market_memory.latest(session, user.tenant_id)
        guide = knowledge.answer(message, lang, memory)
        out.update(answer=guide["answer"], concepts=guide["concepts"], related=guide["related"], used_market_memory=guide["used_market_memory"])
        facts = [guide["answer"]]
    elif name == "coach":
        review = await _coach_review(session, user, lang, 30, "ALL")
        facts = coach.summary_lines(lang, review)
        out.update(answer="\n".join(facts), coach={"stats": review["stats"], "grade": review["grade"], "score": review["score"], "flags": review["flags"][:4]})
    elif name == "interview":
        start = interview.start(message)
        facts = [start["intro"]]
        out.update(answer=start["intro"], prefill=start["prefill"], prompt=message)
    else:
        brief = await briefing.build(session, user, lang)
        facts = briefing.deployment_lines(lang, brief) if name == "deployments" else briefing.summary_lines(lang, brief)
        if name == "deployments":
            facts = briefing.summary_lines(lang, brief)[:2] + facts
        out.update(answer="\n".join(facts), brief={"day_type": brief["day_type"], "plan": brief["plan"], "checklist": brief["checklist"],
                                                   "deployments": brief["deployments"]})
    provider = await ai_settings.provider_for(session, await _tenant(session, user))
    if provider.name != "rule_based" and name != "interview":
        text = await copilot.narrate(provider, lang, name, message, facts)
        await ai_settings.mark_used(session, user.tenant_id, error=None if text else "AI provider unavailable; answered from the rules")
        await session.commit()
        if text:
            out.update(answer=text, source="ai")
        else:
            out["note"] = "AI provider unavailable; answered from the rules"
    return out


@router.post("/copilot")
async def ask_copilot(body: CopilotBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """One box for everything - see `copilot_answer`."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    return await copilot_answer(session, user, body.message, body.language)


# --- Phase AW the strategist: live market study -> validated strategies ----------------------------------

class StrategistBody(BaseModel):
    symbol: str = Field(default="NIFTY 50", min_length=1, max_length=50)
    exchange: str = Field(default="NSE", max_length=10)
    broker: Optional[str] = Field(default=None, max_length=20)
    candles: Optional[List[OHLCVBar]] = Field(default=None, description="1-minute candles (sample mode); omitted = fetched from your broker")
    style: str = Field(default="intraday", pattern=r"^(intraday|scalping)$")
    direction: str = Field(default="auto", pattern=r"^(auto|long|short|both)$")
    language: str = Field(default="mr", pattern=r"^(en|mr)$")
    # Phase BF: a plain-words request in Marathi or English ("बँक निफ्टी फक्त long scalping"); whatever it
    # names overrides the fields above, and its script sets the reply language.
    request: Optional[str] = Field(default=None, max_length=300)

    def resolved(self) -> Tuple["StrategistBody", Optional[dict]]:
        if not (self.request or "").strip():
            return self, None
        parsed = strategist.parse_request(self.request, default_symbol=self.symbol)
        update = {"language": "mr"} if parsed["language"] == "mr" else {}       # Latin-only text keeps the form's language
        for key in ("symbol", "style", "direction"):
            if key in parsed["matched"]:
                update[key] = parsed[key]
        effective = self.model_copy(update=update)
        parsed["summary"] = strategist.request_summary(effective.language, {**parsed, **{k: getattr(effective, k) for k in ("symbol", "style", "direction")}})
        return effective, parsed


class StrategistParseBody(BaseModel):
    request: str = Field(min_length=1, max_length=300)
    symbol: str = Field(default="NIFTY 50", min_length=1, max_length=50)
    language: str = Field(default="mr", pattern=r"^(en|mr)$")


async def _strategist_frames(session: AsyncSession, user: User, body: StrategistBody):
    """1-minute candles (and daily ones from the broker) for the study."""
    if body.candles:
        return interview.frame_from_candles(body.candles[-6000:]), None, "candles"
    from app.brokers.token_lifecycle import build_adapter
    from app.market_data.candles_routes import _pick_record
    from app.market_data.service import MarketDataService
    record = await _pick_record(session, user.tenant_id, body.broker, "primary")
    adapter = build_adapter(record)
    symbol, exchange = body.symbol.strip().upper(), body.exchange.strip().upper()
    try:
        bars = await MarketDataService(adapter, lookback_days=12).get_candles(symbol, exchange, "1min")
    except Exception as exc:  # noqa: BLE001 - say what the broker said
        raise HTTPException(status_code=502, detail=f"Could not fetch {symbol} candles from {record.broker_name}: {type(exc).__name__}: {exc}"[:300]) from exc
    if not bars:
        raise HTTPException(status_code=422, detail=f"{record.broker_name} returned no candles for {symbol}")
    day = None
    try:
        day_bars = await MarketDataService(adapter, lookback_days=400).get_candles(symbol, exchange, "day")
        day = bars_to_dataframe(day_bars) if day_bars else None
        if day is not None and day.index.tz is None:
            day.index = day.index.tz_localize("UTC")
    except Exception:  # noqa: BLE001 - daily bars only add the daily trend and previous-day levels
        day = None
    df = bars_to_dataframe(bars)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df, day, f"broker:{record.broker_name}"


@router.post("/strategist/parse")
async def strategist_parse(body: StrategistParseBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase BF: what the strategist understood from a plain-words request (Marathi or English) - symbol,
    style, direction, language - so the trader can see and correct it before the study runs."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    parsed = strategist.parse_request(body.request, default_symbol=body.symbol)
    parsed["summary"] = strategist.request_summary(parsed["language"] if parsed["language"] == "mr" else body.language, parsed)
    return parsed


@router.post("/strategist/study")
async def strategist_study(body: StrategistBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """The live market study of one symbol: multi-timeframe trend, levels, bias, scenarios."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    body, parsed = body.resolved()
    df, day, source = await _strategist_frames(session, user, body)
    memory = await market_memory.latest(session, user.tenant_id)
    try:
        out = await run_in_threadpool(market_study.study, df, body.symbol, body.language, day=day, memory=memory)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    out["data_source"] = source
    out["request_parsed"] = parsed
    return out


@router.post("/strategist/build")
async def strategist_build(body: StrategistBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Study the market, then write, tune and validate strategies for it (walk-forward on the recent
    sessions) and return the best three as ready-to-adopt plans. With an AI provider, its own rule sets
    are validated alongside."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    body, parsed = body.resolved()
    df, day, source = await _strategist_frames(session, user, body)
    memory = await market_memory.latest(session, user.tenant_id)
    try:
        study = await run_in_threadpool(market_study.study, df, body.symbol, body.language, day=day, memory=memory)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    cfg = await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
    extra = []
    provider = await ai_settings.provider_for(session, await _tenant(session, user))
    if provider.name != "rule_based":
        extra = await strategist.ai_proposals(provider, study, body.style)
        await ai_settings.mark_used(session, user.tenant_id, error=None)
        await session.commit()
    result = await run_in_threadpool(strategist.build, df, study, body.language, style=body.style, direction=body.direction, risk=cfg,
                                     extra_configs=extra)
    return {"study": study, **result, "data_source": source, "ai_candidates": len(extra), "provider": provider.name, "request_parsed": parsed,
            "language": body.language}


class AdoptBody(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    config: dict
    symbol: str = Field(default="NIFTY 50", max_length=50)


@router.post("/strategist/adopt", status_code=201)
async def strategist_adopt(body: AdoptBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session)) -> dict:
    """Save a strategist candidate as one of your custom strategies (versioned), ready to deploy in PAPER."""
    from app.audit.log import write_audit_log
    from app.custom_strategies import versioning
    from app.db.models import CustomStrategyRecord
    from app.plans.limits import check_can_add_custom_strategy
    from app.strategy_engine.declarative import CustomStrategyConfig
    await require_flag(session, "ai_copilot", user.tenant_id)
    try:
        config = CustomStrategyConfig.model_validate({**body.config, "name": body.name.strip()})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid strategy: {exc}"[:300]) from exc
    tenant = await _tenant(session, user)
    await check_can_add_custom_strategy(session, tenant)
    record = CustomStrategyRecord(tenant_id=tenant.id, user_id=user.id, name=config.name, config_json=config.model_dump_json(),
                                  origin="ai-strategist", ai_approved_by=user.id)
    session.add(record)
    await session.flush()
    await versioning.create_version(session, record, config, user, source="ai-strategist")
    await write_audit_log(session, tenant.id, user.id, "ai_strategist_adopted", f"strategy {record.id} ({config.name}) for {body.symbol}")
    await session.commit()
    strategy_id = f"custom:{record.id}"
    return {"strategy_id": strategy_id, "name": config.name,
            "deployment": {"strategy_id": strategy_id, "symbol": body.symbol.strip().upper(), "exchange": "NSE", "timeframe": config.timeframe,
                           "mode": "PAPER", "holding": "INTRADAY", "exit_rules": {"time_exit_at": "15:10", "break_even_at_r": 1.0}}}


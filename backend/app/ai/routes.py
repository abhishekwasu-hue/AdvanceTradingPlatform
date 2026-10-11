"""Phase L: `/api/ai/*` - provider settings, the strategy generator with its review gate, the
regime classifier and the monitoring agent's action queue. Every mutating call is a human's."""
import copy
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import evidence
from app.ai.rate_limit import ai_rate_limit
from app.ai import advisor, briefing, coach, copilot, generator, market_study, strategist, global_cues, interview, knowledge, market_memory, monitor, settings as ai_settings, thesis
from app.ai import compliance_terms as terms
from app.ai.compliance_terms import ai_acknowledged, require_ai_acknowledged
from app.ai.providers import ProviderError
from app.ai.regime import REGIMES, classify_regime
from app.auth.dependencies import current_session_id, ensure_live_step_up, get_current_user, require_owner, require_trader
from app.backtest.engine import ENGINE_VERSION, run_backtest
from app.backtest.routes import _run_summary
from app.core.models import OHLCVBar, RiskConfig, bars_to_dataframe
from app.db.models import AiActionRecord, AiCandidateRecord, AiStrategyDraftRecord, BacktestRunRecord, StrategyDeploymentRecord, Tenant, TradeRecord, User
from app.db.session import get_session
from app.plans.limits import require_feature
from app.risk_engine.routes import get_tenant_risk_config
from app.strategy_engine.declarative import DeclarativeStrategy
from app.platform.controls import flag_enabled, require_flag

router = APIRouter(prefix="/api/ai", tags=["ai"], dependencies=[Depends(ai_rate_limit)])      # H-C1 b


class ProviderBody(BaseModel):
    provider: str = Field(pattern=r"^(anthropic|openai|rule_based)$")
    model: Optional[str] = Field(default=None, max_length=80)
    api_key: Optional[str] = Field(default=None, min_length=8, max_length=300)
    enabled: bool = True
    # P0.8-D / DPDP: the owner confirms the data-sharing consent (what leaves the platform, where it goes, how to opt out)
    # before an external provider is saved. Not needed for rule_based.
    data_consent: bool = False
    language: str = Field(default="en", pattern=r"^(en|mr)$")


class GenerateBody(BaseModel):
    prompt: str = Field(min_length=10, max_length=4000)
    # Phase V3: the reply language, the regime the page read and the symbol the idea is about.
    language: str = Field(default="en", min_length=2, max_length=5, pattern=r"^[A-Za-z-]+$")
    regime: Optional[str] = Field(default=None, max_length=20)
    symbol: Optional[str] = Field(default=None, max_length=50)


class DraftBacktestBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=50)
    exchange: str = Field(default="NSE", max_length=10)
    base_timeframe: str = Field(default="1min", max_length=10)
    # H-C1 a: omitted = fetched by the server (the only evidence that can approve); posted = a sample run.
    candles: Optional[List[OHLCVBar]] = Field(default=None, min_length=30)
    lookback_days: int = Field(default=30, ge=1, le=400)
    broker: Optional[str] = Field(default=None, max_length=20)
    risk_config: Optional[RiskConfig] = None
    data_source: str = Field(default="uploaded", max_length=30, description="ignored for posted candles (recorded as sample)")


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
    from app.ai import metering
    tenant = await _tenant(session, user)
    consent = terms.status_dict(terms.KIND_DATA, await terms.latest(session, terms.KIND_DATA, tenant_id=user.tenant_id))
    return {**ai_settings.as_dict(await ai_settings.get_config(session, user.tenant_id), tenant, usage=await metering.budget_state(session, tenant)),
            "data_consent": consent}


@router.put("/provider")
async def put_provider(body: ProviderBody, request: Request, user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> dict:
    tenant = await _tenant(session, user)
    if body.provider != "rule_based":
        require_feature(tenant, "ai_features", "External AI providers")
        # The current consent is asked once (and again when its text changes); a later save - a model change, a new key,
        # switching it off - does not ask again.
        if await terms.latest(session, terms.KIND_DATA, tenant_id=user.tenant_id) is None:
            if not body.data_consent:
                raise HTTPException(status_code=400, detail={"code": "data_consent_required", "version": terms.DATA_CONSENT_VERSION,
                                                             "message": "Read the data-sharing consent and confirm it (data_consent) before an external provider is saved"})
            await terms.accept(session, user, terms.KIND_DATA, terms.DATA_CONSENT_VERSION, language=body.language, request=request)
    try:
        record = await ai_settings.save_config(session, user, provider=body.provider, model=body.model, api_key=body.api_key, enabled=body.enabled)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from app.ai import metering
    consent = terms.status_dict(terms.KIND_DATA, await terms.latest(session, terms.KIND_DATA, tenant_id=user.tenant_id))
    return {**ai_settings.as_dict(record, tenant, usage=await metering.budget_state(session, tenant)), "data_consent": consent}


@router.delete("/provider", status_code=204)
async def delete_provider(user: User = Depends(require_owner), session: AsyncSession = Depends(get_session)) -> None:
    await ai_settings.delete_config(session, user)


# --- L2 generator + review gate ------------------------------------------------------------------

class AcknowledgementBody(BaseModel):
    version: str = Field(min_length=4, max_length=24)
    language: str = Field(default="en", pattern=r"^(en|mr)$")


def _ai_language(user: User) -> str:
    """P0.9: the language the AI answers in - the user's setting, English by default."""
    lang = getattr(user, "ai_language", None) or "en"
    return lang if lang in ("en", "mr") else "en"


class PreferencesBody(BaseModel):
    ai_language: str = Field(pattern=r"^(en|mr)$")


@router.get("/preferences")
async def get_preferences(user: User = Depends(get_current_user)) -> dict:
    """The user's AI preferences. The dashboard is English; only the AI's written answers follow `ai_language`."""
    return {"ai_language": _ai_language(user), "languages": [{"code": "en", "label": "English"}, {"code": "mr", "label": "Marathi"}]}


@router.put("/preferences")
async def put_preferences(body: PreferencesBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    row = await session.get(User, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    row.ai_language = body.ai_language
    await session.commit()
    return {"ai_language": row.ai_language, "languages": [{"code": "en", "label": "English"}, {"code": "mr", "label": "Marathi"}]}


@router.get("/acknowledgement")
async def get_acknowledgement(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """P0.8-D: the current AI Copilot acknowledgement text (en/mr), its version and whether this user accepted it."""
    row = await terms.latest(session, terms.KIND_COPILOT, tenant_id=user.tenant_id, user_id=user.id)
    return terms.status_dict(terms.KIND_COPILOT, row)


@router.post("/acknowledgement")
async def accept_acknowledgement(body: AcknowledgementBody, request: Request, user: User = Depends(get_current_user),
                                 session: AsyncSession = Depends(get_session)) -> dict:
    """Records that this user read and accepted the current version (audited: `ai_copilot_terms_accepted`)."""
    try:
        row = await terms.accept(session, user, terms.KIND_COPILOT, body.version, language=body.language, request=request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return terms.status_dict(terms.KIND_COPILOT, row)


@router.post("/drafts", status_code=201)
async def generate_draft(body: GenerateBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session), _ack: None = Depends(ai_acknowledged)) -> dict:
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
async def get_draft(draft_id: int, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> dict:
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
    if body.candles:
        df, source = bars_to_dataframe(body.candles), evidence.client_source(body.data_source)
    else:
        df, source = await evidence.server_frame(session, user.tenant_id, body.symbol, body.exchange, body.base_timeframe,
                                                 broker=body.broker, lookback_days=body.lookback_days)
    result = run_backtest(copy.copy(strategy), df, body.symbol.upper(), body.base_timeframe, risk)
    headline = {k: getattr(result, k, None) for k in ("total_trades", "win_rate", "net_pnl", "max_drawdown", "profit_factor", "expectancy", "gross_pnl", "total_charges")}
    headline["analytics"] = result.analytics
    run = BacktestRunRecord(tenant_id=user.tenant_id, user_id=user.id, strategy_id=f"ai_draft_{draft.id}", symbol=body.symbol.upper(),
                            base_timeframe=body.base_timeframe, params_json=None, exit_rules=result.exit_rules, data_source=source,
                            bars=len(df), data_from=df.index[0].to_pydatetime(), data_to=df.index[-1].to_pydatetime(),
                            engine_version=ENGINE_VERSION, metrics_json=json.dumps(headline, default=str))
    session.add(run)
    await session.flush()
    try:
        await generator.attach_backtest(session, draft, run)
    except generator.GenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"draft": generator.as_dict(draft), "run": _run_summary(run), "result": result.model_dump()}


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(draft_id: int, body: ApproveBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
                        _ack: None = Depends(ai_acknowledged)) -> dict:
    draft = await _draft(session, draft_id, user)
    tenant = await _tenant(session, user)
    run = await session.get(BacktestRunRecord, draft.backtest_run_id) if draft.backtest_run_id else None
    if run is not None:                       # no run at all: the generator's own "backtest first" refusal below
        evidence.require_server(run.data_source, "Approving an AI draft")   # H-C1 a
    try:
        record = await generator.approve(session, draft, user, tenant, name=body.name, accept_risk=body.accept_risk)
    except generator.GenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"draft": generator.as_dict(draft), "custom_strategy_id": record.id, "strategy_id": f"custom:{record.id}", "origin": record.origin}


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
    # H-C1 a: omitted = fetched by the server; posted = a sample plan (its options cannot be deployed).
    candles: Optional[List[OHLCVBar]] = Field(default=None, min_length=interview.MIN_BARS, max_length=20_000)
    data_source: str = Field(default="sample", max_length=30, description="ignored for posted candles (recorded as sample)")
    symbol: Optional[str] = Field(default=None, max_length=50, description="server fetch: defaults to the interview's symbol")
    exchange: str = Field(default="NSE", max_length=10)
    broker: Optional[str] = Field(default=None, max_length=20)
    lookback_days: Optional[int] = Field(default=None, ge=1, le=400, description="server fetch window; None = 30 days")
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
    match: int = Field(default=0, ge=0, le=100, description="ignored since P0.8-D (no match score); kept for older clients")


@router.get("/interview/questions")
async def interview_questions(user: User = Depends(get_current_user)) -> dict:
    return {"questions": [q.as_dict() for q in interview.QUESTIONS], "feedback_options": advisor.feedback_options()}


@router.post("/interview/start")
async def interview_start(body: InterviewStartBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> dict:
    """Is the request vague (interview first) or a rule description (generate directly)? Plus the
    answers the text already gives, so they are not asked again, and the saved profile (Phase AQ)."""
    out = interview.start(body.prompt)
    saved, prefs = await advisor.load_profile(session, user)
    out["profile"] = None if saved is None else {"answers": saved, "preferences": prefs.model_dump()}
    return out


def _check_timeframe(body: InterviewPlanBody) -> None:
    if interview.timeframe_minutes(body.base_timeframe) is None or body.base_timeframe == "day":
        raise HTTPException(status_code=400, detail=f"Use intraday candles for the interview, not {body.base_timeframe!r}")


async def _df_for(session: AsyncSession, user: User, body: InterviewPlanBody):
    """(frame, data_source): posted candles are a sample; otherwise the server fetches them (H-C1 a)."""
    _check_timeframe(body)
    if body.candles:
        return interview.frame_from_candles(body.candles[-interview.MAX_BARS:]), evidence.client_source(body.data_source)
    symbol = body.symbol or body.answers.symbol
    df, source = await evidence.server_frame(session, user.tenant_id, symbol, body.exchange, body.base_timeframe, broker=body.broker,
                                             lookback_days=body.lookback_days or 30)
    return df.tail(interview.MAX_BARS), source


async def _options(session: AsyncSession, user: User, answers: interview.InterviewAnswers, prefs: advisor.Preferences,
                   body: InterviewPlanBody) -> dict:
    from app.platform.controls import risk_ceilings
    df, data_source = await _df_for(session, user, body)
    ceilings = await risk_ceilings(session)
    memory = await market_memory.latest(session, user.tenant_id)   # Phase AR: background for the plan
    # The evidence step walks strategies bar by bar (seconds of CPU): off the event loop.
    result = await run_in_threadpool(advisor.build_options, answers, prefs, df, body.base_timeframe, ceilings=ceilings,
                                     data_source=data_source, memory=memory)
    await advisor.save_profile(session, user, answers, advisor.Preferences.model_validate(result["preferences"]))
    # P0.8 / A3: each option's deployment is held on the server; "Deploy in PAPER" takes the candidate id and the
    # trader's risk acceptance, never the browser's copy of the plan.
    now = datetime.now(timezone.utc)
    for option in result.get("options") or []:
        pick = option.get("recommended") or {}
        if not option.get("deployment"):
            continue
        row = AiCandidateRecord(tenant_id=user.tenant_id, user_id=user.id, source="interview", symbol=str(option["deployment"].get("symbol") or answers.symbol).upper(),
                                name=str(pick.get("name") or option["deployment"].get("strategy_id") or "interview plan")[:120],
                                strategy_id=option["deployment"].get("strategy_id"), config_json=None,
                                metrics_json=json.dumps({"evidence": pick.get("evidence"), "regime_fit": pick.get("regime_fit"), "score": pick.get("score"),
                                                         "option": option.get("option"), "data_source": data_source}, default=str),
                                deployment_json=json.dumps(option["deployment"], default=str), risk_json=json.dumps(option.get("risk_config") or {}, default=str),
                                expires_at=now + CANDIDATE_TTL, created_at=now)
        session.add(row)
        await session.flush()
        option["candidate_id"] = row.id
    await session.commit()
    balanced = next((o for o in result.get("options") or [] if (o.get("option") or {}).get("id") == "balanced"), None)
    result["candidate_id"] = balanced.get("candidate_id") if balanced else None
    return result


CANDIDATE_TTL = timedelta(days=7)


class CandidateDeployBody(BaseModel):
    candidate_id: int
    accept_risk: bool = Field(default=False, description="P0.8 / A3: the human confirms the maximum loss per trade before anything runs")


async def _candidate(session: AsyncSession, candidate_id: int, user: User, source: str) -> AiCandidateRecord:
    row = await session.get(AiCandidateRecord, candidate_id)
    if row is None or row.tenant_id != user.tenant_id or row.source != source:
        raise HTTPException(status_code=404, detail="No such candidate - build the plan again")
    if row.status != "OPEN":
        raise HTTPException(status_code=409, detail=f"This candidate was already {row.status.lower()}")
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="This candidate has expired - build the plan again on today's market")
    return row


def _risk_statement(risk: dict) -> str:
    capital = float(risk.get("capital") or 0.0)
    pct = float(risk.get("risk_per_trade_pct") or 0.0)
    return f"the maximum loss per trade of about {capital * pct / 100:,.0f} INR ({pct:g}% of {capital:,.0f})" if capital and pct else "the maximum loss per trade"


@router.post("/interview/deploy", status_code=201)
async def interview_deploy(body: CandidateDeployBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
                           session_id: Optional[int] = Depends(current_session_id), _ack: None = Depends(ai_acknowledged)) -> dict:
    """P0.8 / A3: deploy an interview option in PAPER through the same gate as an AI draft - the candidate is the
    server's, its strategy carries backtest evidence with trades, and the human accepted the maximum loss per trade."""
    from app.deployments.routes import DeploymentCreateRequest, create_deployment
    await require_flag(session, "ai_copilot", user.tenant_id)
    row = await _candidate(session, body.candidate_id, user, "interview")
    metrics = json.loads(row.metrics_json or "{}")
    evidence.require_server(metrics.get("data_source"), "Deploying an interview option")      # H-C1 a
    tested = (metrics.get("evidence") or {})
    if not tested.get("tested") or int(tested.get("total_trades") or 0) <= 0:
        raise HTTPException(status_code=400, detail="This option has no backtest evidence with trades on the server - run the plan on more candles first")
    risk = json.loads(row.risk_json or "{}")
    if not body.accept_risk:
        raise HTTPException(status_code=400, detail=f"Confirm that you accept {_risk_statement(risk)} before deploying (accept_risk)")
    payload = json.loads(row.deployment_json or "{}")
    payload["mode"] = "PAPER"
    request = DeploymentCreateRequest(**payload)
    created = await create_deployment(request, user=user, session=session, session_id=session_id)
    row.status, row.deployment_id = "DEPLOYED", created.id
    from app.audit.log import write_audit_log
    await write_audit_log(session, user.tenant_id, user.id, "ai_candidate_deployed", f"candidate {row.id} ({row.name}) -> PAPER deployment #{created.id}, risk accepted")
    await session.commit()
    return {"candidate_id": row.id, "deployment": created.model_dump(), "mode": "PAPER"}


@router.post("/interview/plan")
async def interview_plan(body: InterviewPlanBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> dict:
    """Three risk settings (safe / balanced / active) on templates the trader chooses between (P0.8-D: described and
    ranked by data, never recommended; no match %): market read, template with its evidence, risk settings, R:R,
    contract and a PAPER deployment - shown, never applied. The answers are remembered in the trader profile."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    _check_timeframe(body)
    prefs = body.preferences
    if prefs is None:
        _, prefs = await advisor.load_profile(session, user)
    prefs = prefs.model_copy(update={"match_history": []})
    return await _options(session, user, body.answers, prefs, body)


@router.post("/interview/refine")
async def interview_refine(body: InterviewRefineBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> dict:
    """"Not this one, because..." - the reasons become preferences and the options are rebuilt."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    bad = [c for c in body.feedback if c not in advisor.FEEDBACK_CODES]
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown feedback {bad}; valid: {list(advisor.FEEDBACK_CODES)}")
    _check_timeframe(body)
    prefs = body.preferences
    if prefs is None:
        _, prefs = await advisor.load_profile(session, user)
    answers, prefs, changes = advisor.apply_feedback(body.answers, prefs, body.feedback, body.strategy_id, body.option_id)
    result = await _options(session, user, answers, prefs, body)
    result["changes"] = changes
    return result


@router.post("/interview/choose")
async def interview_choose(body: InterviewChooseBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> dict:
    """Remembers the option the trader picked; the next round leans towards it."""
    _, prefs = await advisor.load_profile(session, user)
    prefs.chosen = (prefs.chosen + [{"at": datetime.now(timezone.utc).isoformat(), "option": body.option_id,
                                     "strategy_id": body.strategy_id}])[-20:]
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
    language: str = Field(default="en", pattern=r"^(en|mr)$")


def _with_global(out: dict, lang: str) -> dict:
    from app.ai import sentiment
    out["sentiment_view"] = sentiment.view(lang, out.get("sentiment"))     # Phase BC
    out["global_view"] = global_cues.view(lang, out.get("globals", []))
    out["global_source"] = global_cues.SOURCE_NOTE_MR if lang == "mr" else global_cues.SOURCE_NOTE_EN
    out["global_gift_note"] = global_cues.GIFT_NOTE_MR if lang == "mr" else global_cues.GIFT_NOTE_EN
    out["global_enabled"] = global_cues.enabled()
    return out


@router.get("/market-memory")
async def get_market_memory(symbol: Optional[str] = Query(default=None, max_length=50), language: str = Query(default="en", pattern=r"^(en|mr)$"),
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
async def concepts(language: str = Query(default="en", pattern=r"^(en|mr)$"), user: User = Depends(get_current_user)) -> dict:
    """The guide's concept library (titles), for browsing."""
    return {"concepts": knowledge.catalogue(language)}


@router.get("/concepts/{concept_id}")
async def concept(concept_id: str, language: str = Query(default="en", pattern=r"^(en|mr)$"), user: User = Depends(get_current_user)) -> dict:
    found = knowledge.BY_ID.get(concept_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such concept")
    return found.as_dict(language)


@router.post("/ask")
async def ask(body: AskBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> dict:
    """Ask the guide: answered from the concept library and the market memory, or - with an external
    AI provider configured - by the AI grounded on the same notes, the memory and the trader's profile."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    lang = body.language or _ai_language(user)
    memory = await market_memory.latest(session, user.tenant_id)
    saved, _ = await advisor.load_profile(session, user)
    provider = await ai_settings.provider_for(session, await _tenant(session, user), task="knowledge", user_id=user.id)
    if provider.name == "rule_based":
        out = knowledge.answer(body.question, lang, memory)
        if getattr(provider, "reason", ""):
            out["note"] = f"AI not used ({provider.reason}); answered from the concept library"
        return out
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


async def _action_is_live(session: AsyncSession, row: AiActionRecord) -> bool:
    """P0.8 / A2: a proposal touches real money when its deployment or its position runs LIVE."""
    if row.deployment_id is not None:
        dep = await session.get(StrategyDeploymentRecord, row.deployment_id)
        if dep is not None and dep.mode == "LIVE":
            return True
    if row.trade_id is not None:
        trade = await session.get(TradeRecord, row.trade_id)
        if trade is not None and trade.mode == "LIVE":
            return True
    return False


@router.post("/actions/{action_id}/approve")
async def approve_action(action_id: int, body: NoteBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
                         session_id: Optional[int] = Depends(current_session_id)) -> dict:
    """Approve and execute in one human step (pause / acknowledge). Exits need a live price, so
    an EXIT_POSITION approval executes through the worker's price source when available and
    otherwise tells the user to close from the Positions page. P0.8 / A2: a LIVE deployment or
    position needs the authenticator step-up the Telegram message promises (same rule as creating
    or resuming a LIVE deployment); A1: a LIVE exit squares off at the broker the trade went through."""
    row = await _action(session, action_id, user)
    if await _action_is_live(session, row):
        await ensure_live_step_up(session, user, session_id, "Approving an AI proposal on a LIVE deployment")
    try:
        row = await monitor.decide(session, row, user, approve=True, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    price_lookup = broker = None
    if row.action == "EXIT_POSITION":
        from app.ai.worker_bridge import price_lookup_for_tenant
        from app.trading.position_monitor import broker_for_trade
        price_lookup = await price_lookup_for_tenant(session, user.tenant_id)
        trade = await session.get(TradeRecord, row.trade_id) if row.trade_id else None
        broker = await broker_for_trade(session, trade)
    row = await monitor.execute(session, row, user, price_lookup=price_lookup, broker=broker)
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
async def daily_brief(language: str = Query(default="en", pattern=r"^(en|mr)$"), user: User = Depends(require_ai_acknowledged),
                      session: AsyncSession = Depends(get_session)) -> dict:
    """Today's briefing: the day type and game plan, the session, your P&L and risk budget, why each
    deployment is or is not trading, and the pre-trade checklist."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    return await briefing.build(session, user, language)


# --- Phase BD-lite: the market thesis (shadow overlay only) --------------------------------------------

@router.get("/thesis/history")
async def thesis_history(symbol: Optional[str] = Query(default=None, max_length=50), limit: int = Query(default=30, ge=1, le=200),
                         user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Stored theses newest first and the scoreboard (hit rate over the scored ones). The shadow
    multiplier is reported, never applied."""
    await require_flag(session, thesis.FLAG, user.tenant_id)
    return await thesis.history(session, user.tenant_id, symbol, limit=limit)


@router.get("/thesis/report")
async def thesis_report(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase BD-2: this week's thesis scoreboard as the Friday notification would state it (read-only preview)."""
    await require_flag(session, thesis.FLAG, user.tenant_id)
    report = await thesis.weekly_report(session, user.tenant_id)
    return report or {"scored": 0, "lines": ["No thesis scored in the last 7 days yet."], "title": None}


@router.get("/thesis/{symbol}")
async def thesis_for(symbol: str, language: str = Query(default="en", pattern=r"^(en|mr)$"), refresh: bool = Query(default=False),
                     narrate: bool = Query(default=False, description="ask the organisation's own AI provider for a narrative (numbers-checked)"),
                     user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> dict:
    """The thesis of one watched symbol from the market memory: direction, agreement matrix, bull/base/bear
    scenarios, the shadow size multiplier (recorded, never applied) and the rule-based sentences. 404
    until the memory has a read of the symbol."""
    await require_flag(session, thesis.FLAG, user.tenant_id)
    if len(symbol) > 50:
        raise HTTPException(status_code=422, detail="Symbol too long")
    provider = None
    if narrate:
        try:
            provider = await ai_settings.provider_for(session, await _tenant(session, user), task="thesis", user_id=user.id)
        except ProviderError:                       # a stale provider record: the rule-based narrative, with the reason
            provider = None
    news_items = []
    from app.news_feed import service as news_feed_service
    if await news_feed_service.enabled(session, user.tenant_id):
        news_items = await news_feed_service.items(session, user.tenant_id, hours=24)
    out = await thesis.current(session, user.tenant_id, symbol, lang=language, refresh=refresh or narrate, news_items=news_items, provider=provider)
    if out is None:
        raise HTTPException(status_code=404, detail=f"No market read of {symbol.upper()} yet - add it to the watchlist and refresh the market memory")
    if narrate and out.get("narrative_source") == "model":
        await ai_settings.mark_used(session, user.tenant_id)
    if provider is not None:
        await session.commit()            # P0.8-C: the metered usage of the narration, grounded or not
    return out


@router.get("/coach")
async def trade_coach(language: str = Query(default="en", pattern=r"^(en|mr)$"), days: int = Query(default=30, ge=1, le=365),
                      mode: str = Query(default="ALL", pattern=r"^(ALL|PAPER|LIVE)$"), user: User = Depends(require_ai_acknowledged),
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
    provider = await ai_settings.provider_for(session, await _tenant(session, user), task="knowledge", user_id=user.id)
    if provider.name != "rule_based" and name != "interview":
        text, why = await copilot.narrate(provider, lang, name, message, facts)
        await ai_settings.mark_used(session, user.tenant_id, error=None if text else f"AI answer not used ({why}); answered from the rules")
        await session.commit()
        if text:
            out.update(answer=text, source="ai", numbers=copilot.number_sources(text, facts, message))      # H-C1 d
        else:
            out["note"] = f"AI answer not used ({why}); answered from the rules"
        from app.ai import metering
        out["usage"] = metering.spent_by(provider)
    elif getattr(provider, "reason", "") and name != "interview":
        out["note"] = f"AI not used ({provider.reason}); answered from the rules"
    return out


class AgentAskBody(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    language: Optional[str] = Field(default=None, pattern=r"^(en|mr)$")


@router.post("/agent/ask")
async def agent_ask(body: AgentAskBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> dict:
    """H-C2 (ADR-0019, behind the `ai_agent` flag, off by default): the AI answers after reading typed, read-only tools;
    when the trader asks for it, it may file one guarded proposal (pause / risk reduction / review) for a person to decide.
    A provider without tool use (the rules, or one not wired yet) gets the ordinary Copilot answer. Never an order."""
    from app.ai import agent
    from app.ai.prompt_versions import stamp
    from app.ai.tools import ToolContext
    await require_flag(session, "ai_copilot", user.tenant_id)
    await require_flag(session, "ai_agent", user.tenant_id)
    lang = body.language or _ai_language(user)
    provider = await ai_settings.provider_for(session, await _tenant(session, user), task="knowledge", user_id=user.id)
    if not getattr(provider, "supports_tools", False):
        out = await copilot_answer(session, user, body.question, lang)
        out["agent"] = {"used": False, "reason": "the configured AI provider does not support tools here; ordinary Copilot answer"}
        return out
    stamp(provider, agent.PROMPT_VERSION)
    answer = await agent.run_agent(provider, ToolContext(session, user.tenant_id, user), body.question, lang=lang)
    from app.ai import metering
    return {"answer": answer.text, "source": answer.source, "language": lang, "note": answer.note, "numbers": answer.numbers,
            "proposals": answer.proposals,                                   # PROPOSED - decided under AI Copilot, never auto-run
            "agent": {"used": True, "run_id": answer.run_id, "stopped": answer.stopped,
                      "tools": [{k: c[k] for k in ("name", "ok", "as_of", "duration_ms")} for c in answer.tool_calls]},
            "usage": metering.spent_by(provider)}


@router.post("/copilot")
async def ask_copilot(body: CopilotBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> dict:
    """One box for everything - see `copilot_answer`."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    return await copilot_answer(session, user, body.message, body.language or _ai_language(user))


# --- Phase AW the strategist: live market study -> validated strategies ----------------------------------

class StrategistBody(BaseModel):
    symbol: str = Field(default="NIFTY 50", min_length=1, max_length=50)
    exchange: str = Field(default="NSE", max_length=10)
    broker: Optional[str] = Field(default=None, max_length=20)
    candles: Optional[List[OHLCVBar]] = Field(default=None, description="1-minute candles (sample mode); omitted = fetched from your broker")
    style: str = Field(default="intraday", pattern=r"^(intraday|scalping)$")
    direction: str = Field(default="auto", pattern=r"^(auto|long|short|both)$")
    language: str = Field(default="en", pattern=r"^(en|mr)$")
    # Phase BF: a plain-words request in Marathi or English ("बँक निफ्टी फक्त long scalping"); whatever it
    # names overrides the fields above, and its script sets the reply language.
    request: Optional[str] = Field(default=None, max_length=300)

    def resolved(self) -> Tuple["StrategistBody", Optional[dict]]:
        if not (self.request or "").strip():
            return self, None
        parsed = strategist.parse_request(self.request, default_symbol=self.symbol)
        update: dict = {}      # P0.9: a Marathi request is still understood; the reply stays in the dashboard's language
        for key in ("symbol", "style", "direction"):
            if key in parsed["matched"]:
                update[key] = parsed[key]
        effective = self.model_copy(update=update)
        parsed["summary"] = strategist.request_summary(effective.language, {**parsed, **{k: getattr(effective, k) for k in ("symbol", "style", "direction")}})
        return effective, parsed


class StrategistParseBody(BaseModel):
    request: str = Field(min_length=1, max_length=300)
    symbol: str = Field(default="NIFTY 50", min_length=1, max_length=50)
    language: str = Field(default="en", pattern=r"^(en|mr)$")


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
async def strategist_parse(body: StrategistParseBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> dict:
    """Phase BF: what the strategist understood from a plain-words request (Marathi or English) - symbol,
    style, direction, language - so the trader can see and correct it before the study runs."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    parsed = strategist.parse_request(body.request, default_symbol=body.symbol)
    parsed["summary"] = strategist.request_summary(body.language, parsed)      # P0.9: understood in any script, summarised in the UI's language
    return parsed


@router.post("/strategist/study")
async def strategist_study(body: StrategistBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> dict:
    """The live market study of one symbol: multi-timeframe trend, levels, bias, scenarios."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    body, parsed = body.resolved()
    df, day, source = await _strategist_frames(session, user, body)
    memory = await market_memory.latest(session, user.tenant_id)
    try:
        out = await run_in_threadpool(market_study.study, df, body.symbol, body.language, day=day, memory=memory,
                                        stock_detail=await flag_enabled(session, "thesis_stock_targets", user.tenant_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    out["data_source"] = source
    out["request_parsed"] = parsed
    return out


@router.post("/strategist/build")
async def strategist_build(body: StrategistBody, user: User = Depends(require_ai_acknowledged), session: AsyncSession = Depends(get_session)) -> dict:
    """Study the market, then write, tune and validate strategies for it (walk-forward on the recent
    sessions) and return the best three as ready-to-adopt plans. With an AI provider, its own rule sets
    are validated alongside."""
    await require_flag(session, "ai_copilot", user.tenant_id)
    body, parsed = body.resolved()
    df, day, source = await _strategist_frames(session, user, body)
    memory = await market_memory.latest(session, user.tenant_id)
    try:
        study = await run_in_threadpool(market_study.study, df, body.symbol, body.language, day=day, memory=memory,
                                        stock_detail=await flag_enabled(session, "thesis_stock_targets", user.tenant_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    cfg = await get_tenant_risk_config(user.tenant_id, session) or RiskConfig()
    extra = []
    provider = await ai_settings.provider_for(session, await _tenant(session, user), task="strategist", user_id=user.id)
    if provider.name != "rule_based":
        extra = await strategist.ai_proposals(provider, study, body.style)
        await ai_settings.mark_used(session, user.tenant_id, error=None)
        await session.commit()
    result = await run_in_threadpool(strategist.build, df, study, body.language, style=body.style, direction=body.direction, risk=cfg,
                                     real_data=str(source).startswith("broker"),
                                     extra_configs=extra)
    # P0.8 / A3: the validated candidates are held on the server; "adopt" takes a candidate id, never a config.
    now = datetime.now(timezone.utc)
    for plan in result.get("candidates") or []:
        row = AiCandidateRecord(tenant_id=user.tenant_id, user_id=user.id, source="strategist", symbol=str(study["symbol"]).upper(), name=str(plan["name"])[:120],
                                config_json=json.dumps(plan["config"]),
                                metrics_json=json.dumps({k: plan.get(k) for k in ("in_sample", "out_of_sample", "all", "verdict", "oos_sessions", "source")}, default=str),
                                deployment_json=json.dumps(plan.get("deployment"), default=str), risk_json=cfg.model_dump_json(),
                                expires_at=now + CANDIDATE_TTL, created_at=now)
        session.add(row)
        await session.flush()
        plan["candidate_id"] = row.id
    await session.commit()
    return {"study": study, **result, "data_source": source, "ai_candidates": len(extra), "provider": provider.name, "request_parsed": parsed,
            "language": body.language}


class AdoptBody(BaseModel):
    candidate_id: int
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    accept_risk: bool = Field(default=False, description="P0.8 / A3: the human confirms the maximum loss per trade")


@router.post("/strategist/adopt", status_code=201)
async def strategist_adopt(body: AdoptBody, user: User = Depends(require_trader), session: AsyncSession = Depends(get_session),
                           _ack: None = Depends(ai_acknowledged)) -> dict:
    """Save a strategist candidate as one of your custom strategies (versioned), ready to deploy in PAPER.
    P0.8 / A3: the candidate is the server's (built and validated by /strategist/build - a config typed by the
    browser is not accepted), it must carry a simulation with trades, it runs the compliance checklist like an AI
    draft, and the human accepts the maximum loss per trade. Only then does it get the `ai-strategist` lineage."""
    from app.ai.compliance import evaluate_config
    from app.audit.log import write_audit_log
    from app.custom_strategies import versioning
    from app.db.models import CustomStrategyRecord
    from app.plans.limits import check_can_add_custom_strategy
    from app.strategy_engine.declarative import CustomStrategyConfig
    await require_flag(session, "ai_copilot", user.tenant_id)
    candidate = await _candidate(session, body.candidate_id, user, "strategist")
    metrics = json.loads(candidate.metrics_json or "{}")
    if int(((metrics.get("all") or {}).get("trades")) or 0) <= 0 or metrics.get("verdict") == "untested":
        raise HTTPException(status_code=400, detail="This candidate has no server-side simulation with trades - it cannot be adopted")
    if int(((metrics.get("out_of_sample") or {}).get("trades")) or 0) <= 0:      # P0.9: "untested" became "insufficient"/"sample"
        raise HTTPException(status_code=400, detail="This candidate took no trades on the unseen sessions - it cannot be adopted")
    try:
        config = CustomStrategyConfig.model_validate({**json.loads(candidate.config_json or "{}"), "name": (body.name or candidate.name).strip()})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid strategy: {exc}"[:300]) from exc
    tenant = await _tenant(session, user)
    cfg, ceilings = await generator._effective_risk(session, tenant.id)
    config, report = evaluate_config(config, cfg, ceilings)
    if not report.ok:
        raise HTTPException(status_code=400, detail=f"Resolve the compliance failures first: {report.failure_text()}"[:600])
    if not body.accept_risk:
        statement = report.user_must_accept.get("max_loss_per_trade_text") or _risk_statement(json.loads(candidate.risk_json or "{}"))
        raise HTTPException(status_code=400, detail=f"Confirm that you accept the risk before adopting (accept_risk): {statement}"[:600])
    await check_can_add_custom_strategy(session, tenant)
    record = CustomStrategyRecord(tenant_id=tenant.id, user_id=user.id, name=config.name, config_json=config.model_dump_json(),
                                  origin="ai-strategist", ai_approved_by=user.id)
    session.add(record)
    await session.flush()
    await versioning.create_version(session, record, config, user, source="ai-strategist")
    candidate.status, candidate.adopted_strategy_id = "ADOPTED", record.id
    await write_audit_log(session, tenant.id, user.id, "ai_strategist_adopted",
                          f"candidate {candidate.id} -> strategy {record.id} ({config.name}) for {candidate.symbol}, compliance ok, risk accepted")
    await session.commit()
    strategy_id = f"custom:{record.id}"
    return {"strategy_id": strategy_id, "name": config.name, "candidate_id": candidate.id, "compliance": report.as_dict(),
            "deployment": {"strategy_id": strategy_id, "symbol": candidate.symbol, "exchange": "NSE", "timeframe": config.timeframe,
                           "mode": "PAPER", "holding": "INTRADAY", "exit_rules": {"time_exit_at": "15:10", "break_even_at_r": 1.0}}}


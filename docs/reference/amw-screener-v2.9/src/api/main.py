"""AMW AI Screener REST API (FastAPI) - /api/v1.

Auth: `Authorization: Bearer <token>` (tokens created with `python -m src.api.manage create-user`).
Set AMW_AUTH_MODE=local to allow unauthenticated single-user desktop access (default: token).
Roles: admin (everything), analyst (scan, own data), viewer (read-only).
Analysis only - there is no order-placement endpoint.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Literal

import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from ..analysis.regime import classify_regime
from ..backtest.engine import run_backtest
from ..config import STRATEGIES, TIMEFRAME_INFO, TIMEFRAMES
from ..data.validation import clean_and_validate, usable
from ..screener.scanner import analyse_symbol, apply_filters, signals_frame
from ..services.analyst import analyse, build_evidence
from ..services.app_services import Services
from ..strategies.base import StrategyContext
from ..strategies.registry import enabled_strategies, evaluate_all

STREAM_POLL_S = 2.0     # WebSocket bar stream: database tail interval when no in-process wake-up arrives

log = logging.getLogger("amw.api")


def _clean(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, float) and not np.isfinite(o):
        return None
    if isinstance(o, (np.floating, np.integer)):
        return _clean(o.item())
    if isinstance(o, (pd.Timestamp,)):
        return str(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return o


class RateLimiter:
    def __init__(self, per_minute: int):
        self.n = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now, q = time.time(), self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.n:
            return False
        q.append(now)
        return True


class ScanRequest(BaseModel):
    universe: str = "Custom"
    symbols: str = ""
    strategies: list[str] | None = None
    limit: int | None = Field(default=None, ge=1, le=3000)

    @field_validator("strategies")
    @classmethod
    def _v(cls, v):
        if v and any(s not in STRATEGIES for s in v):
            raise ValueError(f"strategies must be among {STRATEGIES}")
        return v


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    symbols: list[str] = Field(max_length=1000)


class AlertIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    strategies: list[str] = []
    min_score: float = Field(default=0, ge=0, le=100)
    directions: list[Literal["bullish", "bearish"]] = []
    timeframes: list[str] = []
    symbols: list[str] = []
    cooldown_minutes: int = Field(default=120, ge=0, le=10080)
    channels: list[Literal["in_app", "telegram", "email"]] = ["in_app"]


class TradeIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=30)
    strategy: str = ""
    direction: Literal["bullish", "bearish"]
    entry_price: float = Field(gt=0)
    qty: int = Field(gt=0)
    stop: float = Field(gt=0)
    target: float | None = Field(default=None, gt=0)
    entry_time: str
    notes: str = ""
    signal_key: str | None = None


class BacktestIn(BaseModel):
    ticker: str
    timeframe: Literal["1D", "1H"] = "1D"
    strategy: str
    start: str | None = None
    end: str | None = None


class LabStrategyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    language: Literal["python", "pine", "javascript", "typescript", "sql", "lua", "r", "visual"]
    description: str = Field(default="", max_length=2000)
    source: str | None = Field(default=None, max_length=100_000)
    ir: dict | None = None
    spec: dict | None = None


class LabDraftIn(BaseModel):
    source: str | None = Field(default=None, max_length=100_000)
    ir: dict | None = None
    spec: dict | None = None


class LabValidateIn(BaseModel):
    language: Literal["python", "pine", "javascript", "typescript", "sql", "lua", "r", "visual"]
    source: str | None = Field(default=None, max_length=100_000)
    ir: dict | None = None
    spec: dict | None = None


class LabScanIn(BaseModel):
    version: int | None = None
    universe: str = "Custom"
    symbols: str = ""
    limit: int | None = Field(default=None, ge=1, le=3000)
    params: dict | None = None


class LabBacktestIn(BaseModel):
    version: int | None = None
    ticker: str
    timeframe: Literal["1D", "1H"] = "1D"
    params: dict | None = None
    start: str | None = None
    end: str | None = None


class ReportIn(BaseModel):
    signal_key: str = Field(min_length=8, max_length=32)
    format: Literal["json", "csv", "xlsx", "png", "pdf"] = "pdf"
    mode: Literal["quick", "standard", "detailed"] = "standard"


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["scan", "scan_report", "scanner"] = "scan"
    scanner_id: str | None = Field(default=None, max_length=32)
    scanner_version: int | None = None
    universe: str = "Custom"
    symbols: str = Field(default="", max_length=20000)
    strategies: list[str] | None = None
    limit: int | None = Field(default=None, ge=1, le=3000)
    top_n: int = Field(default=3, ge=1, le=20)
    at_time: str | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    trading_days_only: bool = True


class NotifyIn(BaseModel):
    email: str | None = Field(default=None, max_length=200)
    quiet_hours: list[str] | None = None
    max_per_hour: int = Field(default=20, ge=1, le=500)


class OwnBotIn(BaseModel):
    bot_token: str = Field(min_length=20, max_length=100)
    chat_id: str = Field(min_length=1, max_length=40)


class FyersConnectIn(BaseModel):
    app_id: str = Field(min_length=5, max_length=40)
    secret: str = Field(min_length=6, max_length=64)
    redirect_uri: str = Field(min_length=8, max_length=300)


class FyersCallbackIn(BaseModel):
    auth_code: str = Field(min_length=10, max_length=4000)
    state: str = Field(min_length=10, max_length=100)


class FeedStartIn(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=100)
    poll_every_min: int = Field(default=1, ge=1, le=15)


class AnalystIn(BaseModel):
    ticker: str
    timeframes: list[str] = ["1W", "1D", "1H"]
    question: str | None = Field(default=None, max_length=1000)
    use_llm: bool = False
    provider: Literal["openai", "anthropic"] | None = None


class AIKeyIn(BaseModel):
    api_key: str = Field(min_length=20, max_length=400)
    model: str | None = Field(default=None, max_length=100)
    session_only: bool = False


class AIModelIn(BaseModel):
    model: str = Field(min_length=1, max_length=100)


class AIDraftIn(BaseModel):
    description: str = Field(min_length=10, max_length=2000)
    language: Literal["pine", "python"] = "pine"
    provider: Literal["openai", "anthropic"] | None = None
    authorized: bool = False


class AIDualIn(BaseModel):
    ticker: str
    timeframes: list[str] = ["1D"]
    question: str | None = Field(default=None, max_length=1000)
    authorized: bool = False


class ScannerNLIn(BaseModel):
    text: str = Field(min_length=3, max_length=2000)


class ScannerSaveIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    ir: dict
    envelope: dict = Field(default_factory=dict)
    description: str = Field(default="", max_length=2000)
    scanner_id: str | None = Field(default=None, max_length=32)
    publish: bool = True
    note: str = Field(default="", max_length=500)


class ReplayIn(BaseModel):
    as_of: str = Field(min_length=8, max_length=32)
    symbols: list[str] = Field(min_length=1, max_length=200)
    version: int | None = None


class CompareIn(BaseModel):
    version_a: int
    version_b: int
    symbols: list[str] = Field(min_length=1, max_length=10)
    timeframe: Literal["1D", "1H", "15m", "5m", "30m", "1W"] = "1D"


def create_app(services: Services | None = None) -> FastAPI:
    svc = services or Services()
    app = FastAPI(title="AMW AI Custom Stock Screener API", version="1.0.0",
                  description="Analysis only. Data from yfinance is delayed/historical, not real-time.")
    origins = [o for o in os.environ.get("AMW_CORS_ORIGINS", "http://localhost:8501").split(",") if o]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "DELETE", "PUT"],
                       allow_headers=["Authorization", "Content-Type"])
    limiter = RateLimiter(int(os.environ.get("AMW_RATE_LIMIT_PER_MIN", "120")))
    metrics = {"requests": 0, "errors": 0, "latency_ms": deque(maxlen=1000)}
    app.state.svc, app.state.metrics = svc, metrics

    from ..saas.entitlements import EntitlementError

    @app.exception_handler(EntitlementError)
    async def _plan_limit(request: Request, exc: EntitlementError):
        return JSONResponse({"detail": {"code": "plan_limit", "limit": exc.limit, "message": str(exc)}}, status_code=403)

    @app.middleware("http")
    async def mw(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        t = time.perf_counter()
        key = request.headers.get("Authorization", request.client.host if request.client else "anon")
        if request.url.path.startswith("/api/") and not limiter.allow(key):
            return JSONResponse({"error": "rate_limited", "request_id": rid}, status_code=429, headers={"X-Request-ID": rid})
        try:
            resp = await call_next(request)
        except Exception:  # noqa: BLE001
            metrics["errors"] += 1
            log.exception("unhandled error rid=%s", rid)
            resp = JSONResponse({"error": "internal_error", "request_id": rid}, status_code=500)
        metrics["requests"] += 1
        metrics["latency_ms"].append((time.perf_counter() - t) * 1000)
        if resp.status_code >= 500:
            metrics["errors"] += 1
        resp.headers["X-Request-ID"] = rid
        # security headers (AUDIT S-001). Swagger/ReDoc pages need their CDN assets, so they get a relaxed CSP.
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.url.path in ("/docs", "/redoc") or request.url.path.startswith("/docs/"):
            resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        else:
            resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    def user(authorization: str | None = Header(default=None)) -> dict:
        if os.environ.get("AMW_AUTH_MODE", "token") == "local":
            return svc.store.get_user(svc.local_user)
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing bearer token")
        u = svc.store.user_by_token(authorization[7:])
        if not u:
            raise HTTPException(401, "invalid token")
        return u

    def writer(u: dict = Depends(user)) -> dict:
        if u["role"] == "viewer":
            raise HTTPException(403, "viewer role is read-only")
        return u

    def admin(u: dict = Depends(user)) -> dict:
        if u["role"] != "admin":
            raise HTTPException(403, "admin only")
        return u

    # ---------------- health (no auth)
    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        h = svc.health()
        ok = h["database"].get("ok", False)
        return JSONResponse(_clean({"status": "ready" if ok else "not_ready", "database": h["database"]}), status_code=200 if ok else 503)

    # ---------------- metadata
    @app.get("/api/v1/meta")
    def meta(u=Depends(user)):
        cfg = svc.cfg(u["id"])
        return _clean({"timeframes": TIMEFRAME_INFO, "strategies": {k: {"enabled": v["enabled"], "version": v["version"], "timeframes": v["timeframes"]}
                                                                     for k, v in cfg["strategies"].items()},
                       "universes": list(cfg["universe"]["sources"]) + ["Custom"], "provider": svc.provider(cfg).health(),
                       "data_kind": svc.provider(cfg).data_kind})

    @app.get("/api/v1/universes/{name}")
    def universe(name: str, u=Depends(user)):
        uni = svc.universe(name, user_id=u["id"]) if name != "Custom" else None
        if uni is None:
            raise HTTPException(400, "use POST /scans with symbols for custom universes")
        return _clean({"name": uni.name, "source": uni.source, "retrieved_at": uni.retrieved_at, "is_fallback": uni.is_fallback,
                       "count": uni.count, "errors": uni.errors, "symbols": uni.symbols.to_dict("records")})

    # ---------------- market data + chart features
    @app.get("/api/v1/ohlcv/{ticker}")
    def ohlcv(ticker: str, tf: str = Query("1D"), bars: int = Query(300, ge=10, le=5000), u=Depends(user)):
        if tf not in TIMEFRAMES:
            raise HTTPException(400, f"tf must be one of {TIMEFRAMES}")
        cfg = svc.cfg(u["id"])
        p = svc.provider(cfg)
        clean, rep = clean_and_validate(p.fetch_many([ticker], tf).get(ticker), ticker, tf, cfg, data_kind=p.data_kind)
        out = clean.tail(bars).reset_index().rename(columns={clean.index.name or "index": "time"})
        out.columns = ["time"] + list(out.columns[1:])
        out["time"] = out["time"].astype(str)
        return _clean({"ticker": ticker, "timeframe": tf, "provider": p.name, "data_kind": p.data_kind, "quality": rep.as_row(),
                       "retrieved_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "bars": out.to_dict("records")})

    @app.get("/api/v1/features/{ticker}")
    def features(ticker: str, tf: str = Query("1D"), u=Depends(user)):
        cfg = svc.cfg(u["id"])
        p = svc.provider(cfg)
        an, reps = analyse_symbol({tf: p.fetch_many([ticker], tf).get(ticker)}, ticker, [tf], cfg, data_kind=p.data_kind)
        a = an[tf]
        if a is None:
            return JSONResponse(_clean({"status": reps[tf].status, "quality": reps[tf].as_row()}), status_code=422)
        return _clean({"status": "OK", "quality": reps[tf].as_row(), "last": a.last, "trend": a.trend_label, "trend_score": a.trend_score,
                       "regime": {k: v for k, v in classify_regime(a, cfg["analysis"]["compression_ratio"]).items() if k != "rules"},
                       "swings": [{"time": a.time(s.i), "confirmed_at": a.time(s.confirmed_at), "kind": s.kind, "label": s.label, "price": s.price} for s in a.swings[-30:]],
                       "events": [{"time": a.time(e.i), "type": e.type, "direction": e.direction, "level": e.level, "swing_time": a.time(e.swing_i)} for e in a.events[-20:]],
                       "zones": a.zones, "sr": a.sr, "patterns": [{k: v for k, v in pt.items() if k != "points"} for pt in a.patterns],
                       "candles": [{**c, "time": a.time(c["i"])} for c in a.candles],
                       "sweeps": [{**s, "time": a.time(s["i"])} for s in a.liquidity["sweeps"][-10:]]})

    # ---------------- scans (background jobs)
    @app.post("/api/v1/scans", status_code=202)
    def start_scan(req: ScanRequest, u=Depends(writer)):
        uni = svc.universe(req.universe, req.symbols, user_id=u["id"])
        if uni.count == 0:
            raise HTTPException(400, "empty universe")
        job = svc.start_scan(uni.symbols, uni.name, req.strategies, req.limit, user_id=u["id"])
        return {"job_id": job.id, "universe": uni.name, "universe_source": uni.source, "is_fallback": uni.is_fallback, "symbols": uni.count}

    @app.get("/api/v1/jobs")
    def jobs(u=Depends(user)):
        return svc.jobs.list(u["id"], u["role"] == "admin")

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str, u=Depends(user)):
        j = svc.jobs.get(job_id, u["id"], u["role"] == "admin")
        if not j:
            raise HTTPException(404, "job not found")
        out = j.public()
        res = j.result or getattr(j, "partial", None)
        if isinstance(res, dict):                      # background API job (backtest/report): JSON result
            out["result"] = res
        elif res is not None:
            out.update({"symbols_done": res.symbols_done, "total": res.total, "stopped_early": res.stopped_early,
                        "partial": j.status != "done" or res.stopped_early})
        return _clean(out)

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel(job_id: str, u=Depends(writer)):
        if svc.jobs.get(job_id, u["id"], u["role"] == "admin") is None:
            raise HTTPException(404, "job not found")
        return {"cancelled": svc.jobs.cancel(job_id, u["id"], u["role"] == "admin")}

    @app.get("/api/v1/jobs/{job_id}/signals")
    def job_signals(job_id: str, strategy: list[str] = Query(default=[]), direction: str | None = None,
                    min_score: float = 0, include_rejected: bool = False, page: int = Query(1, ge=1),
                    page_size: int = Query(50, ge=1, le=500), u=Depends(user)):
        j = svc.jobs.get(job_id, u["id"], u["role"] == "admin")
        res = (j.result or getattr(j, "partial", None)) if j else None
        if res is None:
            raise HTTPException(404, "no results")
        df = apply_filters(signals_frame(res, include_rejected), {"strategies": strategy, "direction": direction, "min_score": min_score})
        total = len(df)
        chunk = df.iloc[(page - 1) * page_size: page * page_size]
        return _clean({"total": total, "page": page, "page_size": page_size, "items": chunk.replace({np.nan: None}).to_dict("records")})

    # ---------------- signals store
    @app.get("/api/v1/signals")
    def signal_list(ticker: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), u=Depends(user)):
        return _clean(svc.store.signal_history(u["id"], ticker, limit, offset, include_payload=False))

    @app.get("/api/v1/signals/{key}")
    def signal_detail(key: str, u=Depends(user)):
        s = svc.store.get_signal(key, u["id"])
        if not s:
            raise HTTPException(404, "signal not found")
        return _clean({**s, "lifecycle": svc.store.signal_events(key, u["id"])})

    @app.post("/api/v1/evaluate/{ticker}")
    def evaluate(ticker: str, strategies: list[str] = Query(default=[]), u=Depends(user)):
        cfg = svc.cfg(u["id"])
        strats = enabled_strategies(cfg, strategies or None)
        from ..strategies.registry import required_timeframes
        tfs = required_timeframes(strats)
        p = svc.provider(cfg)
        raw = {tf: p.fetch_many([ticker], tf).get(ticker) for tf in tfs}
        an, reps = analyse_symbol(raw, ticker, tfs, cfg, data_kind=p.data_kind)
        bench = {tf: clean_and_validate(p.fetch_many([cfg["benchmark"]], tf).get(cfg["benchmark"]), "B", tf, cfg)[0] for tf in tfs}
        sigs = [s.to_row() for s in evaluate_all(StrategyContext(ticker.replace(".NS", ""), ticker, an, reps, bench, cfg), strats)]
        return _clean({"ticker": ticker, "quality": {tf: r.as_row() for tf, r in reps.items()}, "signals": sigs})

    # ---------------- AI analyst
    @app.post("/api/v1/analyst")
    def analyst(req: AnalystIn, u=Depends(user)):
        cfg = svc.cfg(u["id"])
        p = svc.provider(cfg)
        tfs = [t for t in req.timeframes if t in TIMEFRAMES]
        an, reps = analyse_symbol({tf: p.fetch_many([req.ticker], tf).get(req.ticker) for tf in tfs}, req.ticker, tfs, cfg, data_kind=p.data_kind)
        strats = enabled_strategies(cfg)
        sigs = [s.to_row() for s in evaluate_all(StrategyContext(req.ticker, req.ticker, an, reps, {}, cfg), strats)]
        ev = build_evidence(req.ticker, an, reps, sigs, classify_regime(an.get("1D")) if an.get("1D") else None)
        ev["regime"].pop("rules", None)
        client, extra = None, {}
        if req.use_llm:
            from ..ai.gateway import BudgetExceeded, GatewayLLM
            try:
                b = svc.ai.new_task(u["id"], "chart_question" if req.question else "chart_explanation", expected_calls=1)
                client = GatewayLLM(svc.ai, u["id"], b, req.provider)
            except BudgetExceeded as e:
                extra = {"ai_blocked": str(e)}
        r = analyse(ev, req.question, client, retries=0)
        if client is not None and client.last is not None:
            extra = {"ai": {"provider": client.last.provider, "model": client.last.model, "task_id": client.last.task_id,
                            "error_code": client.last.error_code, "options": client.last.options,
                            "est_cost_usd": client.last.est_cost_usd, "billing": "billed by your provider to your own account"}}
        return _clean({"evidence": ev, "report": r.deterministic, "llm": r.llm_text, "llm_status": r.llm_status,
                       "rejected_numbers": r.rejected_numbers, "latency_s": r.latency_s, **extra})

    # ---------------- BYOK AI providers (keys are never returned)
    from ..ai.gateway import NOTICE, TASK_TYPES, AuthorizationRequired, BudgetExceeded
    from ..ai.providers import LABEL, OFFICIAL, PROVIDERS, ProviderError

    def _aierr(e: Exception) -> HTTPException:
        if isinstance(e, ProviderError):
            return HTTPException(400 if e.code in ("invalid_key", "missing_key", "model_not_found", "bad_request") else 502,
                                 {"code": e.code, "message": str(e)})
        if isinstance(e, AuthorizationRequired):
            return HTTPException(409, {"code": "authorization_required", "message": str(e)})
        if isinstance(e, BudgetExceeded):
            return HTTPException(429, {"code": "budget", "message": str(e)})
        return HTTPException(400, {"code": "invalid", "message": str(e)})

    @app.get("/api/v1/ai/providers")
    def ai_providers(u=Depends(user)):
        return _clean({"notice": NOTICE, "providers": {p: {"label": LABEL[p], "official_url": OFFICIAL[p]} for p in PROVIDERS},
                       "configured": svc.ai.configured(u["id"]), "policy": svc.ai.policy(u["id"]), "task_types": TASK_TYPES})

    @app.put("/api/v1/ai/keys/{provider}")
    def ai_key_put(provider: Literal["openai", "anthropic"], b: AIKeyIn, u=Depends(writer)):
        try:
            models = svc.ai.save_key(u["id"], provider, b.api_key, b.model, b.session_only)
        except ProviderError as e:
            raise _aierr(e)
        conf = svc.ai.configured(u["id"])[provider]
        return _clean({"saved": True, "storage": conf["storage"], "masked": conf["masked"], "model": conf["model"], "models": models[:200]})

    @app.put("/api/v1/ai/keys/{provider}/model")
    def ai_key_model(provider: Literal["openai", "anthropic"], b: AIModelIn, u=Depends(writer)):
        if not svc.store.ai_set_model(u["id"], provider, b.model):
            raise HTTPException(404, "no stored key for this provider")
        return {"model": b.model}

    @app.delete("/api/v1/ai/keys/{provider}")
    def ai_key_del(provider: Literal["openai", "anthropic"], u=Depends(writer)):
        return {"deleted": svc.ai.delete_key(u["id"], provider)}

    @app.put("/api/v1/ai/policy")
    def ai_policy(p: dict, u=Depends(writer)):
        try:
            return _clean(svc.ai.save_policy(u["id"], p))
        except ValueError as e:
            raise _aierr(e)

    @app.get("/api/v1/ai/usage")
    def ai_usage(limit: int = Query(200, ge=1, le=1000), u=Depends(user)):
        return _clean({"summary": svc.ai.usage_summary(u["id"]), "rows": svc.store.ai_usage(u["id"], limit=limit),
                       "note": "est_cost_usd is AMW's local estimate from your own price settings. Token counts come from the "
                               "provider's API response. Your provider's billing dashboard shows the authoritative charges."})

    @app.post("/api/v1/ai/draft-strategy")
    def ai_draft(b: AIDraftIn, u=Depends(writer)):
        from ..ai.agent import draft_strategy
        try:
            r = draft_strategy(svc.ai, u["id"], b.description, b.language, b.provider, b.authorized)
        except Exception as e:  # noqa: BLE001
            raise _aierr(e)
        return _clean(r.__dict__)

    @app.post("/api/v1/ai/dual-review")
    def ai_dual(b: AIDualIn, u=Depends(writer)):
        cfg = svc.cfg(u["id"])
        p = svc.provider(cfg)
        tfs = [t for t in b.timeframes if t in TIMEFRAMES]
        an, reps = analyse_symbol({tf: p.fetch_many([b.ticker], tf).get(b.ticker) for tf in tfs}, b.ticker, tfs, cfg, data_kind=p.data_kind)
        sigs = [s.to_row() for s in evaluate_all(StrategyContext(b.ticker, b.ticker, an, reps, {}, cfg), enabled_strategies(cfg))]
        ev = build_evidence(b.ticker, an, reps, sigs, None)
        from ..services.analyst import SYSTEM, validate_llm_text
        import json as _json
        try:
            out = svc.ai.dual_review(u["id"], SYSTEM.format(lang="English"),
                                     f"EVIDENCE JSON:\n{_json.dumps(ev)}\n\nQUESTION: {b.question or 'Explain the setups and risks.'}",
                                     b.authorized)
        except Exception as e:  # noqa: BLE001
            raise _aierr(e)
        res = {}
        for prov, r in out["results"].items():
            ok, bad = validate_llm_text(r.text or "", ev) if r.ok else (False, [])
            res[prov] = {"ok": r.ok and ok, "text": r.text if r.ok and ok else None, "error_code": r.error_code if not r.ok else (None if ok else "unverified_numbers"),
                         "message": r.message, "rejected_numbers": bad, "model": r.model}
        return _clean({"task_id": out["task_id"], "calls": out["calls"], "est_cost_usd": out["est_cost_usd"], "results": res,
                       "billing": "each provider billed its own request to your account"})

    # ---------------- user data (tenant-scoped)
    @app.get("/api/v1/watchlists")
    def wl(u=Depends(user)):
        return _clean(svc.store.watchlists(u["id"]))

    @app.post("/api/v1/watchlists")
    def wl_save(w: WatchlistIn, u=Depends(writer)):
        svc.store.save_watchlist(u["id"], w.name, w.symbols)
        return {"saved": w.name}

    @app.delete("/api/v1/watchlists/{name}")
    def wl_del(name: str, u=Depends(writer)):
        svc.store.delete_watchlist(u["id"], name)
        return {"deleted": name}

    @app.get("/api/v1/alerts/rules")
    def rules(u=Depends(user)):
        return _clean(svc.store.alert_rules(u["id"]))

    @app.post("/api/v1/alerts/rules")
    def rule_add(a: AlertIn, u=Depends(writer)):
        from ..saas.entitlements import check
        check(svc.store, u["id"], "alert_rules", len(svc.store.alert_rules(u["id"])) + 1)
        return {"id": svc.store.add_alert_rule(u["id"], **a.model_dump())}

    @app.delete("/api/v1/alerts/rules/{rule_id}")
    def rule_del(rule_id: int, u=Depends(writer)):
        svc.store.delete_alert_rule(u["id"], rule_id)
        return {"deleted": rule_id}

    @app.get("/api/v1/alerts/events")
    def alert_events(u=Depends(user)):
        return _clean(svc.store.alert_events(u["id"]))

    @app.get("/api/v1/journal")
    def jl(u=Depends(user)):
        return _clean(svc.store.trades(u["id"]))

    @app.post("/api/v1/journal")
    def jl_add(t: TradeIn, u=Depends(writer)):
        return {"id": svc.store.add_trade(u["id"], mode="paper", **t.model_dump())}

    # ---------------- backtest
    @app.post("/api/v1/backtests")
    def backtest(b: BacktestIn, async_job: bool = Query(False, description="run in the background; poll /api/v1/jobs/{id}"),
                 u=Depends(writer)):
        if async_job:
            if b.strategy not in STRATEGIES or b.strategy == "intraday":
                raise HTTPException(400, "strategy must be breakout/pullback/reversal/trend_continuation/swing")
            j = svc.jobs.submit_api("backtest", u["id"], lambda: _backtest_sync(b, u), params=b.model_dump())
            return {"job_id": j.id, "status": j.status}
        return _backtest_sync(b, u)

    def _backtest_sync(b: BacktestIn, u: dict) -> dict:
        if b.strategy not in STRATEGIES or b.strategy == "intraday":
            raise HTTPException(400, "strategy must be breakout/pullback/reversal/trend_continuation/swing")
        cfg = svc.cfg(u["id"])
        p = svc.provider(cfg)
        clean, rep = clean_and_validate(p.fetch_many([b.ticker], b.timeframe).get(b.ticker), b.ticker, b.timeframe, cfg)
        if not usable(rep):
            raise HTTPException(422, f"data {rep.status}")
        bench, _ = clean_and_validate(p.fetch_many([cfg["benchmark"]], b.timeframe).get(cfg["benchmark"]), "B", b.timeframe, cfg)
        r = run_backtest(clean, b.timeframe, cfg, b.strategy, bench=bench, start=b.start, end=b.end)
        from ..services.notify_events import backtest_done as _btd
        _btd(svc.store, u["id"], r, f"{b.strategy} {b.ticker if hasattr(b, 'ticker') else ''}".strip())
        return _clean({k: (v.to_dict("records") if isinstance(v, pd.DataFrame) else v) for k, v in r.items() if k != "assumptions"}
                      | {"assumptions": r["assumptions"]})

    # ---------------- strategy lab
    from ..lab import engine as lab_engine
    from ..lab.contract import StrategySpec, capabilities
    from ..lab.ir import TEMPLATES as IR_TEMPLATES, NotExportable, export_python, render_strategy
    from ..lab.pine import TEMPLATES as PINE_TEMPLATES
    from ..lab.service import lab_backtest, start_lab_scan
    from ..lab.sqlcheck import TEMPLATES as SQL_TEMPLATES
    from ..lab.templates import CODE_TEMPLATES

    def _spec(lang, spec):
        try:
            return StrategySpec.model_validate({**(spec or {}), "language": lang})
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"invalid spec: {e}")

    @app.get("/api/v1/lab/capabilities")
    def lab_caps(u=Depends(user)):
        return _clean(capabilities())

    @app.get("/api/v1/lab/templates")
    def lab_templates(u=Depends(user)):
        return {"visual": IR_TEMPLATES, "pine": PINE_TEMPLATES, "sql": SQL_TEMPLATES, **CODE_TEMPLATES}

    @app.get("/api/v1/lab/strategies")
    def lab_list(u=Depends(user)):
        return _clean(svc.store.lab_list(u["id"]))

    @app.post("/api/v1/lab/strategies", status_code=201)
    def lab_create(b: LabStrategyIn, u=Depends(writer)):
        from ..lab.contract import language_enabled
        if not language_enabled(b.language):
            raise HTTPException(403, f"{b.language} authoring mode is disabled on this server")
        sp = _spec(b.language, b.spec)
        from ..saas.entitlements import check
        check(svc.store, u["id"], "lab_strategies", len(svc.store.lab_list(u["id"])) + 1)
        try:
            sid = svc.store.lab_create(u["id"], b.name, b.language, b.description, b.source, b.ir, sp.model_dump())
        except Exception as e:  # noqa: BLE001
            raise HTTPException(409, f"could not create: {type(e).__name__}")
        return {"id": sid, "note": "saved as draft; drafts are never executed automatically"}

    @app.get("/api/v1/lab/strategies/{sid}")
    def lab_get(sid: str, u=Depends(user)):
        st = svc.store.lab_get(u["id"], sid)
        if not st:
            raise HTTPException(404, "not found")
        return _clean({**st, "versions": svc.store.lab_versions(u["id"], sid)})

    @app.put("/api/v1/lab/strategies/{sid}")
    def lab_draft(sid: str, b: LabDraftIn, u=Depends(writer)):
        st = svc.store.lab_get(u["id"], sid)
        if not st:
            raise HTTPException(404, "not found")
        sp = _spec(st["language"], b.spec)
        svc.store.lab_save_draft(u["id"], sid, b.source, b.ir, sp.model_dump())
        return {"saved": True}

    @app.delete("/api/v1/lab/strategies/{sid}")
    def lab_del(sid: str, u=Depends(writer)):
        if not svc.store.lab_archive(u["id"], sid):
            raise HTTPException(404, "not found")
        return {"archived": sid}

    @app.post("/api/v1/lab/strategies/{sid}/publish")
    def lab_publish(sid: str, note: str = "", u=Depends(writer)):
        st = svc.store.lab_get(u["id"], sid)
        if not st:
            raise HTTPException(404, "not found")
        v = lab_engine.validate(st["language"], st["draft_source"], st["draft_ir"], _spec(st["language"], st["draft_spec"]), f"user{u['id']}")
        if not v["ok"]:
            raise HTTPException(422, {"message": "validation failed - not published", **v})
        return {"version": svc.store.lab_publish(u["id"], sid, note), "validated": v["stage"]}

    @app.post("/api/v1/lab/validate")
    def lab_validate(b: LabValidateIn, u=Depends(user)):
        return _clean(lab_engine.validate(b.language, b.source, b.ir, _spec(b.language, b.spec), f"user{u['id']}"))

    @app.post("/api/v1/lab/render")
    def lab_render(ir: dict, u=Depends(user)):
        return {"text": render_strategy(ir)}

    @app.post("/api/v1/lab/export/python")
    def lab_export(ir: dict, u=Depends(user)):
        try:
            return {"source": export_python(ir)}
        except NotExportable as e:
            raise HTTPException(422, str(e))

    @app.post("/api/v1/lab/strategies/{sid}/scans", status_code=202)
    def lab_scan(sid: str, b: LabScanIn, u=Depends(writer)):
        uni = svc.universe(b.universe, b.symbols, user_id=u["id"])
        df = uni.symbols.head(b.limit) if b.limit else uni.symbols
        if df.empty:
            raise HTTPException(400, "empty universe")
        try:
            job, run_id = start_lab_scan(svc, u["id"], sid, b.version, df, uni.name, b.params)
        except EntitlementError:
            raise
        except PermissionError as e:
            raise HTTPException(429, str(e))
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))
        return {"job_id": job.id, "run_id": run_id, "universe_source": uni.source, "is_fallback": uni.is_fallback}

    @app.post("/api/v1/lab/strategies/{sid}/backtests")
    def lab_bt(sid: str, b: LabBacktestIn, async_job: bool = Query(False), u=Depends(writer)):
        if async_job:
            if svc.store.lab_get(u["id"], sid) is None:
                raise HTTPException(404, "strategy not found")
            j = svc.jobs.submit_api("lab_backtest", u["id"], lambda: _lab_bt_sync(sid, b, u), params=b.model_dump())
            return {"job_id": j.id, "status": j.status}
        return _lab_bt_sync(sid, b, u)

    def _lab_bt_sync(sid: str, b: LabBacktestIn, u: dict) -> dict:
        cfg = svc.cfg(u["id"])
        p = svc.provider(cfg)
        clean, rep = clean_and_validate(p.fetch_many([b.ticker], b.timeframe).get(b.ticker), b.ticker, b.timeframe, cfg)
        if not usable(rep):
            raise HTTPException(422, f"data {rep.status}")
        try:
            r = lab_backtest(svc, u["id"], sid, b.version, clean, b.timeframe, params=b.params, start=b.start, end=b.end)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))
        return _clean({k: (v.to_dict("records") if isinstance(v, pd.DataFrame) else v) for k, v in r.items()})

    @app.get("/api/v1/lab/runs")
    def lab_runs(sid: str | None = None, u=Depends(user)):
        return _clean(svc.store.lab_runs(u["id"], sid))

    # ---------------- reports (owner-only)
    from fastapi.responses import Response

    @app.post("/api/v1/reports")
    def report_create(b: ReportIn, async_job: bool = Query(False), u=Depends(writer)):
        from ..reports import builder
        if svc.store.get_signal(b.signal_key, u["id"]) is None:
            raise HTTPException(404, "signal not found")

        def gen():
            name, _, meta = builder.generate(svc, u["id"], b.signal_key, b.format, b.mode)
            return {"filename": name, **meta}
        if async_job:
            j = svc.jobs.submit_api("report", u["id"], gen, params=b.model_dump())
            return {"job_id": j.id, "status": j.status}
        try:
            return gen()
        except KeyError:
            raise HTTPException(404, "signal not found")
        except RuntimeError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/v1/reports")
    def report_list(u=Depends(user)):
        return _clean(svc.store.report_list(u["id"]))

    @app.get("/api/v1/reports/{rid}/download")
    def report_download(rid: int, u=Depends(user)):
        from ..reports import builder
        try:
            name, data = builder.read_report(svc, u["id"], rid)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "report not found")
        media = {"pdf": "application/pdf", "png": "image/png", "json": "application/json", "csv": "text/csv",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[name.rsplit(".", 1)[1]]
        return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ---------------- schedules (owner-only)
    @app.get("/api/v1/schedules")
    def sched_list(u=Depends(user)):
        return _clean(svc.store.schedule_list(u["id"]))

    @app.post("/api/v1/schedules", status_code=201)
    def sched_add(b: ScheduleIn, u=Depends(writer)):
        from ..services.scheduler import validate_schedule
        try:
            cfg = {"universe": b.universe, "symbols": b.symbols, "strategies": b.strategies, "limit": b.limit, "top_n": b.top_n,
                   "scanner_id": b.scanner_id, "version": b.scanner_version}
            validate_schedule(svc.store, u["id"], b.kind, b.at_time, b.interval_minutes, svc.cfg(u["id"]), cfg)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"id": svc.store.schedule_add(u["id"], name=b.name, kind=b.kind, config=cfg, at_time=b.at_time,
                                             interval_minutes=b.interval_minutes, trading_days_only=b.trading_days_only, enabled=True)}

    @app.delete("/api/v1/schedules/{sid}")
    def sched_del(sid: int, u=Depends(writer)):
        if not svc.store.schedule_delete(u["id"], sid):
            raise HTTPException(404, "schedule not found")
        return {"deleted": sid}

    # ---------------- notifications (owner-only; bot tokens never returned)
    from ..notify.telegram import TelegramError
    from ..services import alerts as _al

    @app.get("/api/v1/notify")
    def notify_get(u=Depends(user)):
        return _clean({**_al.public_notify(_al.notify_settings(svc.store, u["id"])),
                       "official_bot_available": bool(_al.official_token())})

    @app.put("/api/v1/notify")
    def notify_put(b: NotifyIn, u=Depends(writer)):
        s = _al.notify_settings(svc.store, u["id"])
        s.update(email=b.email or None, quiet_hours=b.quiet_hours or None, max_per_hour=b.max_per_hour)
        try:
            _al.save_notify(svc.store, u["id"], s)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return _clean(_al.public_notify(s))

    @app.post("/api/v1/notify/telegram/official/start")
    def tg_start(u=Depends(writer)):
        if not _al.official_token():
            raise HTTPException(503, "official AMW bot not configured on this server")
        return {"code": _al.start_official_link(svc.store, u["id"]), "instructions": "Send '/start <code>' to the AMW bot, then call verify."}

    @app.post("/api/v1/notify/telegram/official/verify")
    def tg_verify(u=Depends(writer)):
        try:
            return {"chat_id": _al.verify_official_link(svc.store, u["id"], getattr(svc, "telegram_transport", None))}
        except TelegramError as e:
            raise HTTPException(400, {"code": e.code, "message": str(e)})

    @app.put("/api/v1/notify/telegram/own")
    def tg_own(b: OwnBotIn, u=Depends(writer)):
        try:
            return _al.set_own_bot(svc.store, u["id"], b.bot_token, b.chat_id, getattr(svc, "telegram_transport", None))
        except TelegramError as e:
            raise HTTPException(400, {"code": e.code, "message": str(e)})

    @app.delete("/api/v1/notify/telegram")
    def tg_unlink(u=Depends(writer)):
        _al.unlink_telegram(svc.store, u["id"])
        return {"unlinked": True}

    # ---------------- FYERS broker feed (user's own account; data reaches only that user)
    from ..feed.fyers import FyersError

    @app.put("/api/v1/broker/fyers")
    def fy_connect(b: FyersConnectIn, u=Depends(writer)):
        try:
            svc.feed.connect(u["id"], b.app_id, b.secret, b.redirect_uri)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return _clean({"connected": True, "connection": svc.store.broker_public(u["id"], "fyers", now=svc.feed.clock())})

    @app.get("/api/v1/broker/fyers/login-url")
    def fy_login(u=Depends(writer)):
        try:
            return {"url": svc.feed.login_url(u["id"]), "note": "Open, log in to FYERS, then post auth_code and state to /callback"}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/v1/broker/fyers/callback")
    def fy_callback(b: FyersCallbackIn, u=Depends(writer)):
        try:
            exp = svc.feed.complete_login(u["id"], b.auth_code, b.state)
        except FyersError as e:
            raise HTTPException(400, {"code": e.code, "message": str(e)})
        return {"logged_in": True, "token_expires_utc": str(exp)}

    @app.delete("/api/v1/broker/fyers")
    def fy_disconnect(u=Depends(writer)):
        return {"disconnected": svc.feed.disconnect(u["id"])}

    @app.post("/api/v1/feed/start")
    def feed_start(b: FeedStartIn, u=Depends(writer)):
        try:
            c = svc.feed.start(u["id"], b.symbols, b.poll_every_min)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return _clean(c.public())

    @app.post("/api/v1/feed/stop")
    def feed_stop(u=Depends(writer)):
        svc.feed.stop(u["id"])
        return {"stopped": True}

    @app.get("/api/v1/feed/status")
    def feed_status(u=Depends(user)):
        return _clean(svc.feed.status(u["id"]))

    @app.get("/api/v1/feed/bars")
    def feed_bars(symbol: str, since: str | None = None, limit: int = Query(500, ge=1, le=5000), u=Depends(user)):
        return _clean(svc.store.bars_get(u["id"], symbol.upper(), since, limit))

    @app.websocket("/api/v1/stream/bars")
    async def stream_bars(ws: WebSocket):
        """Live stream of the caller's OWN completed 1-minute bars. First client message (within 10 s):
        {"token": "<api token>", "symbols": ["RELIANCE.NS", ...]} (token omitted in AMW_AUTH_MODE=local).
        The token travels in the message body, not the URL, so it never appears in access logs.
        Bars are read from the database (single source of truth), so the stream works no matter which AMW process
        (UI or API) runs the user's collector; an in-process collector only wakes the stream up sooner.
        Messages: {"type": "subscribed"}, {"type": "bar"|"revision", symbol, ts, open, high, low, close, volume, ...},
        {"type": "status", status, detail}."""
        import asyncio as _aio
        await ws.accept()
        try:
            hello = await _aio.wait_for(ws.receive_json(), timeout=10)
        except Exception:  # noqa: BLE001
            await ws.close(code=4401)
            return
        if not isinstance(hello, dict):
            hello = {}
        if os.environ.get("AMW_AUTH_MODE", "token") == "local":
            usr = svc.store.get_user(svc.local_user)
        else:
            usr = svc.store.user_by_token(str(hello.get("token") or ""))
        if not usr:
            await ws.send_json({"type": "error", "message": "unauthorized"})
            await ws.close(code=4401)
            return
        uid = usr["id"]
        syms = [str(x).upper() for x in (hello.get("symbols") or [])][:200]
        sub = svc.feed.hub.subscribe(uid, syms or None, loop=_aio.get_running_loop())
        cursor = await _aio.to_thread(svc.store.bars_cursor, uid)
        st0 = await _aio.to_thread(svc.feed.collector_status, uid)
        last_status = (st0 or {}).get("status")
        await ws.send_json(_clean({"type": "subscribed", "symbols": syms or "all", "status": st0}))
        closed = _aio.Event()

        async def reader():                 # detects client disconnect; client messages (pings) are ignored
            try:
                while True:
                    await ws.receive_text()
            except Exception:  # noqa: BLE001
                closed.set()
        rt = _aio.ensure_future(reader())
        try:
            while not closed.is_set():
                wake: list[dict] = []
                try:
                    wake.append(await _aio.wait_for(sub.aq.get(), timeout=STREAM_POLL_S))
                    while not sub.aq.empty():
                        wake.append(sub.aq.get_nowait())
                except _aio.TimeoutError:
                    pass
                rows, cursor = await _aio.to_thread(svc.store.bars_since, uid, cursor, syms or None)
                for r in rows:
                    await ws.send_json(_clean({"type": "revision" if r.get("revisions") else "bar",
                                               **{k: r[k] for k in ("symbol", "ts", "open", "high", "low", "close", "volume",
                                                                    "source", "revisions")}}))
                for m in wake:                   # in-process status pushes (bars always come from the database)
                    if m.get("type") == "status":
                        await ws.send_json(_clean(m))
                        last_status = m.get("status")
                st = await _aio.to_thread(svc.feed.collector_status, uid)
                cur = (st or {}).get("status")
                if cur != last_status:           # collector in another process changed state
                    await ws.send_json(_clean({"type": "status", "status": cur, "detail": (st or {}).get("detail"),
                                               "runner": (st or {}).get("runner")}))
                    last_status = cur
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            rt.cancel()
            svc.feed.hub.unsubscribe(sub)

    # ---------------- universal scanner layer (spec, NL builder, templates, replay, version comparison)
    from ..scanner import nl as _nl, replay as _rp, spec as _sp, templates as _tpl

    @app.get("/api/v1/scanner/templates")
    def scanner_templates(u=Depends(user)):
        return _tpl.listing()

    @app.get("/api/v1/scanner/templates/{tid}")
    def scanner_template(tid: str, u=Depends(user)):
        try:
            return _tpl.get(tid)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/v1/scanner/nl")
    def scanner_nl(b: ScannerNLIn, u=Depends(user)):
        """Deterministic parse to a validated spec preview (nothing is saved or executed)."""
        return _nl.parse(b.text)

    @app.post("/api/v1/scanners")
    def scanner_save(b: ScannerSaveIn, u=Depends(writer)):
        try:
            return _sp.save(svc, u["id"], b.name, b.ir, b.envelope, b.description, b.scanner_id, b.publish, b.note)
        except ValueError as e:
            raise HTTPException(422, str(e))
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/v1/scanners/{sid}/duplicate")
    def scanner_dup(sid: str, name: str = Query(..., min_length=1, max_length=100), u=Depends(writer)):
        try:
            return {"scanner_id": _sp.duplicate(svc, u["id"], sid, name)}
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/v1/scanners/{sid}/share")
    def scanner_share(sid: str, to_username: str = Query(..., min_length=1, max_length=60), u=Depends(writer)):
        """Send a copy of the rules to another user (no signals or data). Unknown recipient and foreign scanner -> 404."""
        try:
            return {"shared": True, "recipient_scanner_id": _sp.share(svc, u["id"], sid, to_username)}
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/v1/scanners/{sid}/replay")
    def scanner_replay(sid: str, b: ReplayIn, u=Depends(user)):
        try:
            return _clean(_rp.replay(svc, u["id"], sid, b.as_of, [x.upper() for x in b.symbols], b.version))
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/v1/scanners/{sid}/compare")
    def scanner_compare(sid: str, b: CompareIn, u=Depends(user)):
        try:
            return _clean(_rp.compare_versions(svc, u["id"], sid, b.version_a, b.version_b, [x.upper() for x in b.symbols], b.timeframe))
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    # ---------------- instrument master
    from ..data import instruments as _ins

    @app.get("/api/v1/instruments")
    def instruments_search(q: str = Query(..., min_length=1, max_length=60), limit: int = Query(20, ge=1, le=100), u=Depends(user)):
        return _clean(_ins.search(svc.store, q, limit))

    @app.get("/api/v1/instruments/resolve")
    def instruments_resolve(text: str = Query(..., min_length=1, max_length=120), u=Depends(user)):
        return _clean(_ins.resolve(svc.store, text))

    # ---------------- paper forward tests (simulation of each qualified signal; no orders)
    @app.get("/api/v1/forward")
    def forward_list(status: str | None = None, limit: int = Query(200, ge=1, le=5000), u=Depends(user)):
        from ..services.forward import stats
        return _clean({"stats": stats(svc.store, u["id"]), "items": svc.store.forward_list(u["id"], status, limit)})

    @app.post("/api/v1/forward/mark")
    def forward_mark(u=Depends(writer)):
        from ..services.forward import mark
        return mark(svc, u["id"])

    @app.get("/api/v1/data-source")
    def data_source_get(u=Depends(user)):
        """Selected vs. actually active data source (FYERS falls back to the default when not logged in today)."""
        return svc.data_source_status(u["id"])

    @app.put("/api/v1/data-source")
    def data_source(source: Literal["default", "fyers"], u=Depends(writer)):
        svc.store.set_pref(u["id"], "data_source", source)
        return svc.data_source_status(u["id"])

    # ---------------- plan
    @app.get("/api/v1/plan")
    def plan(u=Depends(user)):
        from ..saas.entitlements import commercial_status, plan_limits, usage
        return _clean({"plan": u.get("plan"), "limits": plan_limits(u.get("plan"), svc.cfg(u["id"])),
                       "usage": usage(svc.store, svc, u["id"]),
                       "note": "AI API usage is billed by your AI provider (BYOK) and is not part of the plan.",
                       **commercial_status(svc.cfg(u["id"]))})

    @app.put("/api/v1/admin/reports/{rid}/legal-hold")
    def report_hold(rid: int, user_id: int = Query(...), hold: bool = Query(True), u=Depends(admin)):
        """Admin only: a report on legal hold is never removed by retention or quota cleanup."""
        if not svc.store.report_set_hold(user_id, rid, hold):
            raise HTTPException(404, "report not found")
        return {"report_id": rid, "legal_hold": hold}

    # ---------------- admin / ops
    @app.get("/api/v1/admin/health")
    def ahealth(u=Depends(admin)):
        lat = list(metrics["latency_ms"])
        return _clean({**svc.health(), "api": {"requests": metrics["requests"], "errors": metrics["errors"],
                                              "p50_ms": float(np.percentile(lat, 50)) if lat else None,
                                              "p95_ms": float(np.percentile(lat, 95)) if lat else None}})

    @app.get("/api/v1/admin/audit")
    def audit(u=Depends(admin)):
        return _clean(svc.store.audit_rows())

    @app.post("/api/v1/billing/webhook")
    async def billing(request: Request):
        """Authoritative billing events only: HMAC-SHA256 of the raw body with AMW_BILLING_WEBHOOK_SECRET."""
        import hashlib
        import hmac
        secret = os.environ.get("AMW_BILLING_WEBHOOK_SECRET")
        if not secret:
            raise HTTPException(503, "billing not configured")
        body = await request.body()
        sig = request.headers.get("X-Signature", "")
        if not hmac.compare_digest(hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), sig):
            raise HTTPException(401, "bad signature")
        import json
        e = json.loads(body)
        applied = svc.store.apply_billing_event(e["id"], int(e["user_id"]), e["type"], e.get("plan"), e)
        return {"applied": applied, "duplicate": not applied}

    return app

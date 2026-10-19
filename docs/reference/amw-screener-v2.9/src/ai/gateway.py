"""BYOK AI gateway: every AI request in AMW goes through `AIGateway`.

Business rules enforced here
* Requests use ONLY the requesting user's own key (persistent encrypted or session-only). There is no company key and
  no environment-variable key; if the user has no valid key the call fails with `missing_key`.
* Provider choice follows the user's policy (single provider, manual, task routing, dual review). A different provider
  is used after a failure ONLY if the user's fallback policy is "auto" and the user has their own key for it.
* Dual Review calls both providers with the user's two keys and requires explicit authorization per task.
* Local safeguards (calls per task, retries, output tokens, per-task / daily / monthly estimated cost and request
  thresholds, repair attempts, recursion depth) are checked BEFORE each provider call. They are AMW-side limits, not
  provider billing limits; the provider account's own usage/billing controls remain authoritative.
* Every attempt (ok / error / blocked) is recorded in `ai_usage` with provider-reported tokens and AMW's estimate,
  stored and displayed separately.
"""
from __future__ import annotations

import copy
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..storage.db import Store, utcnow
from .providers import CLIENTS, LABEL, PROVIDERS, Completion, ProviderError, USER_MESSAGES, Transport
from .redact import redact

NOTICE = ("AI API usage is billed separately by your selected AI provider. You must supply your own API key and maintain "
          "your provider account's billing and usage limits. AI API charges are not included in your AMW subscription. "
          "Using multiple providers may generate separate charges.")

TASK_TYPES = {
    "chart_explanation": "AI Chart Analyst explanation",
    "chart_question": "Question about a chart",
    "strategy_draft": "AI strategy drafting (generate + repair loop)",
    "dual_review": "Dual Review (both providers)",
    "connection_test": "Connection test (model list; no token charges)",
}

DEFAULT_POLICY = {
    "selection": "default",              # default | manual | task_routing
    "default_provider": None,            # openai | anthropic
    "task_routes": {},                   # {task_type: provider}
    "fallback": "none",                  # none | ask | auto  (auto = use your OTHER configured provider after a failure)
    "dual_review_enabled": False,
    "limits": {
        "max_calls_per_task": 4,
        "max_retries": 1,
        "max_output_tokens": 1200,
        "max_cost_per_task_usd": None,
        "daily_cost_usd": None,
        "monthly_cost_usd": None,
        "daily_requests": 50,
        "monthly_requests": 1000,
        "max_repair_attempts": 2,
        "max_agent_depth": 1,
        "confirm_calls_above": 3,        # tasks that may make more calls than this need explicit authorization
    },
    "prices": {},                        # {"openai:<model>": {"input_per_m": x, "output_per_m": y, "cached_input_per_m": z}} USD
}


class BudgetExceeded(RuntimeError):
    pass


class AuthorizationRequired(RuntimeError):
    pass


def merge_policy(p: dict | None) -> dict:
    out = copy.deepcopy(DEFAULT_POLICY)
    for k, v in (p or {}).items():
        if k == "limits" and isinstance(v, dict):
            out["limits"].update(v)
        else:
            out[k] = v
    return out


def validate_policy(p: dict) -> list[str]:
    errs = []
    if p["selection"] not in ("default", "manual", "task_routing"):
        errs.append("selection must be default, manual or task_routing")
    if p["default_provider"] not in (None, *PROVIDERS):
        errs.append("default_provider must be openai or anthropic")
    if p["fallback"] not in ("none", "ask", "auto"):
        errs.append("fallback must be none, ask or auto")
    for t, prov in (p.get("task_routes") or {}).items():
        if t not in TASK_TYPES or prov not in PROVIDERS:
            errs.append(f"invalid task route {t} -> {prov}")
    L = p["limits"]
    for k in ("max_calls_per_task", "max_output_tokens", "daily_requests", "monthly_requests"):
        if not isinstance(L.get(k), int) or L[k] < 1:
            errs.append(f"limits.{k} must be a positive integer")
    for k in ("max_retries", "max_repair_attempts", "max_agent_depth", "confirm_calls_above"):
        if not isinstance(L.get(k), int) or L[k] < 0:
            errs.append(f"limits.{k} must be a non-negative integer")
    if L.get("max_retries", 0) > 5 or L.get("max_repair_attempts", 0) > 5 or L.get("max_agent_depth", 0) > 3:
        errs.append("retries/repairs are capped at 5 and agent depth at 3")
    if L.get("max_output_tokens", 0) > 64000:
        errs.append("limits.max_output_tokens must be <= 64000")
    for k in ("max_cost_per_task_usd", "daily_cost_usd", "monthly_cost_usd"):
        if L.get(k) is not None and (not isinstance(L[k], (int, float)) or L[k] <= 0):
            errs.append(f"limits.{k} must be positive or empty")
    for mk, pr in (p.get("prices") or {}).items():
        if ":" not in mk or mk.split(":")[0] not in PROVIDERS:
            errs.append(f"price key {mk} must be '<provider>:<model>'")
        for f in ("input_per_m", "output_per_m"):
            if not isinstance((pr or {}).get(f), (int, float)) or pr[f] < 0:
                errs.append(f"price {mk}.{f} must be a non-negative number")
    return errs


def estimate_cost(policy: dict, provider: str, model: str, inp: int | None, out: int | None, cached: int | None) -> tuple[float | None, str]:
    """AMW-side estimate from USER-ENTERED prices. None when no price is configured. Never the provider's invoice."""
    pr = (policy.get("prices") or {}).get(f"{provider}:{model}")
    if not pr or inp is None or out is None:
        return None, "no price configured for this model" if not pr else "provider returned no token counts"
    c_in = pr.get("cached_input_per_m", pr["input_per_m"])
    cached = cached or 0
    cost = ((inp - cached) * pr["input_per_m"] + cached * c_in + out * pr["output_per_m"]) / 1e6
    return round(cost, 6), f"user-entered prices USD/1M tokens in {pr['input_per_m']}, cached {c_in}, out {pr['output_per_m']}"


class SessionKeys:
    """Session-only credentials: kept in backend memory, never persisted; lost on logout/restart."""

    def __init__(self):
        self._d: dict[tuple[int, str], tuple[str, str | None]] = {}
        self._lock = threading.Lock()

    def put(self, user_id: int, provider: str, key: str, model: str | None):
        with self._lock:
            self._d[(user_id, provider)] = (key, model)

    def get(self, user_id: int, provider: str):
        return self._d.get((user_id, provider))

    def delete(self, user_id: int, provider: str) -> bool:
        with self._lock:
            return self._d.pop((user_id, provider), None) is not None

    def providers(self, user_id: int) -> list[str]:
        return [p for (u, p) in self._d if u == user_id]

    def clear_user(self, user_id: int):
        with self._lock:
            for k in [k for k in self._d if k[0] == user_id]:
                del self._d[k]


@dataclass
class TaskBudget:
    """Bounded execution for one user task / agent workflow."""
    task_id: str
    task_type: str
    limits: dict
    calls: int = 0
    est_cost: float = 0.0
    depth: int = 0
    stopped: str | None = None
    events: list[str] = field(default_factory=list)


@dataclass
class AIResult:
    ok: bool
    text: str | None
    provider: str | None
    model: str | None
    task_id: str
    error_code: str | None = None
    message: str | None = None
    calls: int = 0
    est_cost_usd: float | None = None
    usage: list[dict] = field(default_factory=list)
    options: list[str] = field(default_factory=list)      # what the user can do next after a failure


class AIGateway:
    def __init__(self, store: Store, transport: Transport | None = None, session_keys: SessionKeys | None = None,
                 sleeper=None):
        self.store = store
        self.transport = transport
        self.session = session_keys or SessionKeys()
        import time
        self._sleep = sleeper or time.sleep

    # ---------------- policy
    def policy(self, user_id: int) -> dict:
        return merge_policy(self.store.get_pref(user_id, "ai_policy", {}))

    def save_policy(self, user_id: int, p: dict) -> dict:
        """Partial update on top of the user's CURRENT policy (unspecified fields keep their values)."""
        cur = self.policy(user_id)
        upd = dict(p)
        if isinstance(upd.get("limits"), dict):
            upd["limits"] = {**cur["limits"], **upd["limits"]}
        m = merge_policy({**cur, **upd})
        errs = validate_policy(m)
        if errs:
            raise ValueError("; ".join(errs))
        self.store.set_pref(user_id, "ai_policy", m)
        self.store.audit("ai_policy", f"user {user_id} selection={m['selection']} fallback={m['fallback']} dual={m['dual_review_enabled']}", str(user_id))
        return m

    # ---------------- credentials
    def configured(self, user_id: int) -> dict[str, dict]:
        out = {c["provider"]: {**c, "storage": "encrypted"} for c in self.store.ai_credentials(user_id)}
        for p in self.session.providers(user_id):
            key, model = self.session.get(user_id, p)
            from .redact import mask
            out[p] = {"provider": p, "masked": mask(key), "model": model, "storage": "session-only"}
        return out

    def _key(self, user_id: int, provider: str) -> tuple[str, str | None]:
        s = self.session.get(user_id, provider)
        if s:
            return s
        try:
            got = self.store.ai_get_key(user_id, provider)
        except Exception as e:  # noqa: BLE001 - decryption failure must not leak details
            raise ProviderError("missing_key", f"Stored {LABEL[provider]} key cannot be used ({type(e).__name__}); re-enter it.") from None
        if not got:
            raise ProviderError("missing_key", USER_MESSAGES["missing_key"] + f" ({LABEL[provider]})")
        return got

    def test_key(self, provider: str, api_key: str) -> list[str]:
        """Validate a NEW key against the provider's free model-list endpoint. Raises ProviderError."""
        from .providers import looks_like_key
        bad = looks_like_key(provider, api_key)
        if bad:
            raise ProviderError("invalid_key", bad)
        return CLIENTS[provider](api_key.strip(), self.transport, timeout=20).list_models()

    def save_key(self, user_id: int, provider: str, api_key: str, model: str | None, session_only: bool = False) -> list[str]:
        """Test first; persist (encrypted) or keep session-only ONLY if the provider accepted the key."""
        models = self.test_key(provider, api_key)
        if model and models and model not in models:
            raise ProviderError("model_not_found", f"Model '{model}' is not available to this key.")
        model = model or (models[0] if models else None)
        if session_only:
            self.store.ai_delete_credential(user_id, provider)
            self.session.put(user_id, provider, api_key.strip(), model)
        else:
            self.session.delete(user_id, provider)
            self.store.ai_put_credential(user_id, provider, api_key.strip(), model)
        self.store.ai_log(user_id, task_id=None, task_type="connection_test", provider=provider, model=None, status="test",
                          usage_source="none", attempt=1)
        return models

    def delete_key(self, user_id: int, provider: str) -> bool:
        a = self.store.ai_delete_credential(user_id, provider)
        b = self.session.delete(user_id, provider)
        return a or b

    # ---------------- routing
    def resolve_provider(self, user_id: int, task_type: str, requested: str | None = None) -> str:
        p = self.policy(user_id)
        conf = self.configured(user_id)
        if not conf:
            raise ProviderError("missing_key", "No AI provider is configured. Add your own OpenAI or Anthropic key in AI Provider Setup.")
        if p["selection"] == "manual":
            if not requested:
                raise ProviderError("bad_request", "Your policy is 'manual selection': choose a provider for this request.")
            prov = requested
        elif p["selection"] == "task_routing" and task_type in (p.get("task_routes") or {}):
            prov = p["task_routes"][task_type]
        else:
            prov = requested or p["default_provider"] or (list(conf)[0] if len(conf) == 1 else None)
            if prov is None:
                raise ProviderError("bad_request", "Both providers are configured: set a default provider or choose one.")
        if prov not in PROVIDERS:
            raise ProviderError("bad_request", f"Unknown provider {prov}")
        if prov not in conf:
            raise ProviderError("missing_key", f"{LABEL[prov]} is selected but you have not configured your own key for it.")
        return prov

    # ---------------- budget
    def new_task(self, user_id: int, task_type: str, expected_calls: int = 1, authorized: bool = False, depth: int = 0) -> TaskBudget:
        p = self.policy(user_id)
        L = p["limits"]
        if depth > L["max_agent_depth"]:
            raise BudgetExceeded(f"agent recursion depth {depth} exceeds your limit {L['max_agent_depth']}")
        if expected_calls > L["max_calls_per_task"]:
            raise BudgetExceeded(f"task needs up to {expected_calls} AI calls; your limit is {L['max_calls_per_task']} per task")
        if expected_calls > L["confirm_calls_above"] and not authorized:
            raise AuthorizationRequired(f"This task may make up to {expected_calls} AI calls billed to your provider account. Confirm to continue.")
        self._period_check(user_id, L)
        return TaskBudget(secrets.token_hex(8), task_type, L, depth=depth)

    def _period_check(self, user_id: int, L: dict, add_cost: float = 0.0):
        now = utcnow()
        day = self.store.ai_totals(user_id, datetime(now.year, now.month, now.day))
        mon = self.store.ai_totals(user_id, datetime(now.year, now.month, 1))
        if day["requests"] >= L["daily_requests"]:
            raise BudgetExceeded(f"daily AI request threshold reached ({day['requests']}/{L['daily_requests']})")
        if mon["requests"] >= L["monthly_requests"]:
            raise BudgetExceeded(f"monthly AI request threshold reached ({mon['requests']}/{L['monthly_requests']})")
        if L.get("daily_cost_usd") and day["est_cost_usd"] + add_cost > L["daily_cost_usd"]:
            raise BudgetExceeded(f"daily estimated-cost threshold reached (${day['est_cost_usd']:.4f} of ${L['daily_cost_usd']})")
        if L.get("monthly_cost_usd") and mon["est_cost_usd"] + add_cost > L["monthly_cost_usd"]:
            raise BudgetExceeded(f"monthly estimated-cost threshold reached (${mon['est_cost_usd']:.4f} of ${L['monthly_cost_usd']})")

    def _precheck(self, user_id: int, b: TaskBudget, policy: dict, provider: str, model: str, prompt_chars: int, max_out: int):
        if b.stopped:
            raise BudgetExceeded(b.stopped)
        if b.calls >= b.limits["max_calls_per_task"]:
            b.stopped = f"max AI calls per task reached ({b.calls})"
            raise BudgetExceeded(b.stopped)
        worst, _ = estimate_cost(policy, provider, model, prompt_chars // 3 + 50, max_out, 0)
        cap = b.limits.get("max_cost_per_task_usd")
        if cap:
            if worst is None:
                b.stopped = f"a per-task cost limit is set but no price is configured for {provider}:{model}; add prices or remove the limit"
                raise BudgetExceeded(b.stopped)
            if b.est_cost + worst > cap:
                b.stopped = f"next call could exceed your per-task cost limit (${b.est_cost:.4f} + up to ${worst:.4f} > ${cap})"
                raise BudgetExceeded(b.stopped)
        self._period_check(user_id, b.limits, worst or 0.0)

    # ---------------- calls
    def complete(self, user_id: int, b: TaskBudget, system: str, user: str, provider: str | None = None,
                 max_output_tokens: int | None = None, cancel=None) -> AIResult:
        policy = self.policy(user_id)
        try:
            prov = self.resolve_provider(user_id, b.task_type, provider)
        except ProviderError as e:
            self.store.ai_log(user_id, task_id=b.task_id, task_type=b.task_type, provider=provider, status="blocked",
                              error_code=e.code, usage_source="none")
            return AIResult(False, None, provider, None, b.task_id, e.code, str(e), b.calls,
                            options=["Configure your own key in AI Provider Setup", "Continue without AI (deterministic analysis)"])
        res = self._call_with_retries(user_id, b, policy, prov, system, user, max_output_tokens, cancel)
        if res.ok or policy["fallback"] == "none" or res.error_code in ("budget", "cancelled"):
            return self._with_options(res, user_id, prov, policy)
        others = [p for p in self.configured(user_id) if p != prov]
        if policy["fallback"] == "auto" and others:
            b.events.append(f"fallback {prov} -> {others[0]} after {res.error_code}")
            res2 = self._call_with_retries(user_id, b, policy, others[0], system, user, max_output_tokens, cancel, fallback_from=prov)
            return self._with_options(res2, user_id, others[0], policy)
        return self._with_options(res, user_id, prov, policy)

    def _with_options(self, res: AIResult, user_id: int, prov: str, policy: dict) -> AIResult:
        if not res.ok:
            opts = []
            if res.error_code in ("rate_limited", "timeout", "network", "provider_unavailable"):
                opts.append("Retry (within your retry limit)")
            others = [p for p in self.configured(user_id) if p != prov]
            if others:
                opts.append(f"Use your {LABEL[others[0]]} key instead")
            opts.append("Continue with deterministic analysis (no AI call)")
            res.options = opts
        return res

    def _call_with_retries(self, user_id, b, policy, prov, system, user, max_out, cancel, fallback_from=None) -> AIResult:
        try:
            key, model = self._key(user_id, prov)
        except ProviderError as e:
            return AIResult(False, None, prov, None, b.task_id, e.code, str(e), b.calls)
        model = model or ""
        max_out = min(int(max_out or b.limits["max_output_tokens"]), b.limits["max_output_tokens"])
        client = CLIENTS[prov](key, self.transport)
        attempt, last = 0, None
        while attempt <= b.limits["max_retries"]:
            attempt += 1
            if cancel is not None and cancel.is_set():
                return AIResult(False, None, prov, model, b.task_id, "cancelled", "Cancelled by user.", b.calls)
            try:
                self._precheck(user_id, b, policy, prov, model, len(system) + len(user), max_out)
            except BudgetExceeded as e:
                self.store.ai_log(user_id, task_id=b.task_id, task_type=b.task_type, provider=prov, model=model, status="blocked",
                                  error_code="budget", attempt=attempt, usage_source="none", fallback_from=fallback_from)
                return AIResult(False, None, prov, model, b.task_id, "budget", f"Stopped by your local limit: {e}", b.calls,
                                b.est_cost if b.est_cost else None)
            b.calls += 1
            try:
                comp: Completion = client.complete(model, system, user, max_out)
            except ProviderError as e:
                last = e
                self.store.ai_log(user_id, task_id=b.task_id, task_type=b.task_type, provider=prov, model=model, status="error",
                                  error_code=e.code, attempt=attempt, usage_source="none", fallback_from=fallback_from)
                if not e.retryable:
                    break
                self._sleep(min(8.0, 1.5 * attempt))
                continue
            cost, basis = estimate_cost(policy, prov, comp.model, comp.input_tokens, comp.output_tokens, comp.cached_input_tokens)
            if cost:
                b.est_cost += cost
            self.store.ai_log(user_id, task_id=b.task_id, task_type=b.task_type, provider=prov, model=comp.model, status="ok",
                              attempt=attempt, fallback_from=fallback_from, input_tokens=comp.input_tokens,
                              output_tokens=comp.output_tokens, cached_input_tokens=comp.cached_input_tokens,
                              cache_write_tokens=comp.cache_write_tokens,
                              usage_source="provider" if comp.input_tokens is not None else "none",
                              est_cost_usd=cost, price_basis=basis, latency_s=comp.latency_s,
                              provider_request_id=(comp.request_id or "")[:100])
            return AIResult(True, comp.text, prov, comp.model, b.task_id, calls=b.calls, est_cost_usd=b.est_cost or cost,
                            usage=[{"input_tokens": comp.input_tokens, "output_tokens": comp.output_tokens,
                                    "cached_input_tokens": comp.cached_input_tokens}])
        msg = USER_MESSAGES.get(last.code, str(last)) + (f" Details: {redact(str(last), key)}" if last and str(last) else "")
        return AIResult(False, None, prov, model, b.task_id, last.code if last else "unknown", msg, b.calls)

    # ---------------- dual review
    def dual_review(self, user_id: int, system: str, user: str, authorized: bool) -> dict:
        p = self.policy(user_id)
        if not p["dual_review_enabled"]:
            raise AuthorizationRequired("Dual Review is disabled in your AI settings.")
        conf = self.configured(user_id)
        missing = [LABEL[x] for x in PROVIDERS if x not in conf]
        if missing:
            raise ProviderError("missing_key", f"Dual Review needs your own key for both providers; missing: {', '.join(missing)}.")
        if not authorized:
            raise AuthorizationRequired("Dual Review sends the request to BOTH OpenAI and Anthropic. Each provider bills its own "
                                        "request to your account. Confirm to continue.")
        b = self.new_task(user_id, "dual_review", expected_calls=2, authorized=True)
        out = {}
        for prov in PROVIDERS:
            out[prov] = self._call_with_retries(user_id, b, p, prov, system, user, None, None)
        return {"task_id": b.task_id, "results": out, "calls": b.calls, "est_cost_usd": b.est_cost or None}

    # ---------------- usage view
    def usage_summary(self, user_id: int) -> dict:
        now = utcnow()
        return {"today": self.store.ai_totals(user_id, datetime(now.year, now.month, now.day)),
                "month": self.store.ai_totals(user_id, datetime(now.year, now.month, 1)),
                "last_30d": self.store.ai_totals(user_id, now - timedelta(days=30))}


class GatewayLLM:
    """Adapter for services.analyst: a provider-independent LLM client bound to ONE user and ONE task budget."""
    name = "byok"

    def __init__(self, gw: AIGateway, user_id: int, budget: TaskBudget, provider: str | None = None):
        self.gw, self.user_id, self.budget, self.provider = gw, user_id, budget, provider
        self.last: AIResult | None = None

    def complete(self, system: str, user: str, timeout: float = 60) -> str:
        self.last = self.gw.complete(self.user_id, self.budget, system, user, self.provider)
        if not self.last.ok:
            raise RuntimeError(self.last.message)
        return self.last.text or ""

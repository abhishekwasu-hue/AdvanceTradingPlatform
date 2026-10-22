"""Phase L1: the LLM provider seam (master prompt section 56, V4.3) - P0.8-C provider layer.

One small interface - `complete(system, user) -> str` plus `complete_full(...) -> Completion` with the token counts -
and three implementations: Anthropic (Messages API through the official SDK), OpenAI (Chat Completions) and a
deterministic **rule-based** provider that needs no key and no network (it runs the existing NLU parser), so every AI
feature degrades to something explainable when no provider is configured. Keys live per tenant in
`ai_provider_configs`, encrypted at rest, entered under Settings > AI only; nothing here reads them from the
environment or logs them. The AI never sees broker credentials (safety rule 15): a provider gets text in and text
out, nothing else.

P0.8-C:
* C1 - thinking tokens count against `max_tokens`, so the request budget is the caller's *text* budget plus a thinking
  headroom for the effort level; an answer cut off at the limit (`stop_reason == "max_tokens"`, OpenAI
  `finish_reason == "length"`) is retried once with twice the budget and then raised as `ProviderError` - a partial
  answer is never returned as a complete one.
* C2 - model names come from the environment per tier (`AI_<PROVIDER>_STRONG_MODEL` for anything that writes rules,
  `AI_<PROVIDER>_FAST_MODEL` for narration, classification and Q&A); the tenant's Settings model overrides generation.
* C3 - OpenAI requests use `max_completion_tokens`; reasoning models get no temperature; a 400 body is logged (never
  the key) and surfaced.
* C4 - one SDK client per key is reused across calls; the static system prompt is a cacheable block; the request
  timeout grows with the token budget.
* C5 - `Completion` carries the usage so `app.ai.metering` can price and record every call.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

import httpx

from app.core import config

logger = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "openai", "rule_based")
DEFAULT_PROVIDER = "anthropic"   # the operator's choice; a tenant without a key still falls back to rule_based
TIMEOUT = float(getattr(config, "AI_PROVIDER_TIMEOUT_SECONDS", 45.0))
TIMEOUT_PER_1K_TOKENS = float(os.environ.get("AI_TIMEOUT_PER_1K_SECONDS", "8"))
RULE_BASED_MODEL = "nlu-parser-v1"

# C2: which tier each AI task runs on. The strong tier writes rules (strategy drafts, strategist proposals, scanner
# plans); the fast tier narrates and answers questions - a cheaper model at low effort. P0.10: the cheap tier runs the
# frequent, short jobs (news classification, scanner reads) on the smallest model (Haiku-class / nano).
TIERS = ("cheap", "fast", "strong")
TASK_TIERS: Dict[str, str] = {
    "strategy_generation": "strong", "strategist": "strong", "scanner_plan": "strong", "general": "strong",
    "narration": "fast", "copilot": "fast", "knowledge": "fast", "thesis": "fast",
    "classification": "cheap", "scanner_read": "cheap",
    "support": "cheap",          # Z10-f: the support bot rewording one help passage
    "option_chain_ask": "fast",  # OC3: Ask AI on the Option chain writes one analysis spec (the server computes the numbers)
}
EFFORT_FOR_TIER = {"strong": "medium", "fast": "low", "cheap": "low"}
# C1: thinking headroom per effort level (tokens the model may spend reasoning before the text starts).
THINKING_HEADROOM = {"low": 2000, "medium": 6000, "high": 12000, "xhigh": 24000, "max": 32000}
_FALLBACK_MODELS = {"anthropic": {"strong": "claude-opus-5-5", "fast": "claude-sonnet-5-5", "cheap": "claude-haiku-5-5"},
                    "openai": {"strong": "gpt-4.1", "fast": "gpt-4.1-mini", "cheap": "gpt-4.1-nano"},
                    # Settings redesign A (R3): a user's own Gemini key runs the same tiers (only ever a user's key).
                    "gemini": {"strong": "gemini-2.5-pro", "fast": "gemini-2.5-flash", "cheap": "gemini-2.5-flash-lite"},
                    # Master Order 2 part A: the OpenAI-compatible BYOK providers (only ever a user's own key). Sources and
                    # the day they were checked: OPENAI_COMPATIBLE below. `AI_<PROVIDER>_<TIER>_MODEL` overrides each.
                    # OpenRouter: the Free Models Router picks a free model that can do what the request needs (images too).
                    "openrouter": {"strong": "openrouter/free", "fast": "openrouter/free", "cheap": "openrouter/free"},
                    "groq": {"strong": "openai/gpt-oss-120b", "fast": "openai/gpt-oss-20b", "cheap": "openai/gpt-oss-20b"},
                    "mistral": {"strong": "mistral-large-latest", "fast": "mistral-medium-latest", "cheap": "mistral-small-latest"},
                    # Z.ai: GLM-4.5-Flash is its free model; the strong tier needs balance on the account.
                    "zai": {"strong": "glm-4.6", "fast": "glm-4.5-flash", "cheap": "glm-4.5-flash"},
                    "rule_based": {"strong": RULE_BASED_MODEL, "fast": RULE_BASED_MODEL, "cheap": RULE_BASED_MODEL}}


def default_models() -> Dict[str, Dict[str, str]]:
    """Per provider and tier, from the environment (`AI_ANTHROPIC_STRONG_MODEL`, `AI_ANTHROPIC_FAST_MODEL`,
    `AI_ANTHROPIC_CHEAP_MODEL`, and the same three for OpenAI); the built-in names only fill gaps."""
    out: Dict[str, Dict[str, str]] = {}
    for provider, tiers in _FALLBACK_MODELS.items():
        out[provider] = {tier: (os.environ.get(f"AI_{provider.upper()}_{tier.upper()}_MODEL") or name).strip() for tier, name in tiers.items()}
    return out


def model_for(provider: str, tenant_model: Optional[str], task: str) -> str:
    """The model a task runs on: the tenant's Settings model for the strong tier (their choice for generation), the
    operator's fast model for cheap tasks, the environment default otherwise."""
    tier = TASK_TIERS.get(task, "strong")
    models = default_models().get(provider, _FALLBACK_MODELS["rule_based"])
    if tier == "strong" and (tenant_model or "").strip():
        return str(tenant_model).strip()
    return models[tier]


class _DefaultModels(dict):
    """`DEFAULT_MODELS[provider]` - the strong-tier model of each provider, read from the environment on access
    (the Settings card shows it as the suggestion when a tenant leaves the model blank)."""

    def __getitem__(self, provider: str) -> str:  # type: ignore[override]
        return default_models()[provider]["strong"]

    def get(self, provider: str, default: Any = None) -> Any:  # type: ignore[override]
        return default_models().get(provider, {}).get("strong", default)

    def __iter__(self):
        return iter(PROVIDERS)

    def __len__(self) -> int:
        return len(PROVIDERS)

    def __contains__(self, provider: object) -> bool:
        return provider in PROVIDERS

    def items(self):  # type: ignore[override]
        return [(p, self[p]) for p in PROVIDERS]

    def as_dict(self) -> Dict[str, str]:
        return dict(self.items())


DEFAULT_MODELS: Any = _DefaultModels()


def thinking_headroom(effort: str) -> int:
    return THINKING_HEADROOM.get(effort, THINKING_HEADROOM["medium"])


def timeout_for(budget_tokens: int) -> float:
    """C4: a 16k-token strategy draft cannot finish inside the 45 s a short narration gets."""
    return TIMEOUT + TIMEOUT_PER_1K_TOKENS * max(0, budget_tokens) / 1000.0


def is_reasoning_model(model: str) -> bool:
    """C3: OpenAI reasoning models reject `temperature` and only take `max_completion_tokens`. MO2 A: the open-weight
    `openai/gpt-oss-*` models (Groq) reason too and take `reasoning_effort`."""
    m = (model or "").lower()
    return m.startswith(("o1", "o3", "o4", "gpt-5", "openai/gpt-oss", "gpt-oss"))


_GENERATION = re.compile(r"(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d{1,2}))?(?!\d)")   # a date suffix is not a minor version


def _supports_effort(model: str) -> bool:
    """Adaptive thinking + `output_config.effort` exist from the 4.6 generation on (Opus/Sonnet 4.6, everything 5.x);
    Haiku 4.5 and older take neither (they run without thinking here). An id this cannot parse is assumed current."""
    m = (model or "").lower()
    if m.startswith("claude-3"):
        return False
    found = _GENERATION.search(m)
    if not found:
        return True
    major, minor = int(found.group(2)), int(found.group(3) or 0)
    return major >= 5 or (major == 4 and minor >= 6)


def _effort_for(tier: str) -> str:
    return EFFORT_FOR_TIER.get(tier, "medium")


@dataclass
class Completion:
    """One provider answer with what it cost: the text and the token counts the provider reported."""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_reason: str = "end_turn"
    model: str = ""
    provider: str = ""
    cost_usd_override: Optional[float] = None    # metering tests / manual adjustments; None = price from the table

    def add_usage(self, other: "Completion") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens


@dataclass
class ToolCall:
    """One tool the model asked for (H-C2, ADR-0019)."""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ToolTurn:
    """One step of a tool-using conversation: text so far, the tools requested, and the assistant content exactly as
    the provider returned it (thinking blocks included) - it must be sent back unchanged on the next step."""
    text: str
    calls: List["ToolCall"]
    assistant_content: Any
    usage: "Completion"
    stop_reason: str = "end_turn"


# Settings redesign A (R6): what kind of failure a provider error is, read from the provider's own error code, so the
# user reads one plain sentence (app/ai/byok.py PLAIN_ERRORS) instead of the provider's text.
ERROR_KINDS = ("auth", "no_credit", "rate", "model", "unreachable", "no_vision", "other")


class ProviderError(RuntimeError):
    """A provider failure the caller shows as "AI unavailable" and answers from the rules. `usage` carries the tokens
    of the failed attempts when the provider reported them (they are billed). `kind` is one of ERROR_KINDS."""

    def __init__(self, message: str, *, usage: Optional[Completion] = None, kind: str = "other") -> None:
        super().__init__(message)
        self.usage = usage
        self.kind = kind if kind in ERROR_KINDS else "other"


_IMAGE_WORDS = ("image", "vision", "inline_data", "inlinedata", "multimodal")


def _mentions_image(text: str) -> bool:
    low = (text or "").lower()
    return any(word in low for word in _IMAGE_WORDS) and ("support" in low or "not" in low or "invalid" in low)


def _anthropic_status_kind(exc: Any) -> str:
    """Anthropic: a 400 "credit balance is too low" is no credit; 402 too; 403 a key without permission; 529 overloaded."""
    status = int(getattr(exc, "status_code", 0) or 0)
    text = f"{getattr(exc, 'type', '') or ''} {getattr(exc, 'message', '') or ''}".lower()
    if status == 402 or "credit balance" in text or "billing" in text:
        return "no_credit"
    if status in (401, 403):
        return "auth"
    if status in (429, 529) or "overloaded" in text:
        return "rate"
    if status == 404:
        return "model"
    if status == 400 and _mentions_image(text):
        return "no_vision"
    return "other"


def _openai_error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return ""
    return f"{err.get('code') or ''} {err.get('type') or ''}".strip().lower()


# MO2 A: Z.ai's own error codes (docs.z.ai/api-reference/api-code, checked 2026-10-22): 1113 is "insufficient balance or no
# resource package" (HTTP 429), 1302 "rate limit reached for requests" (HTTP 429).
_ZAI_NO_CREDIT_CODES = ("1113",)
_ZAI_RATE_CODES = ("1302",)
_NO_CREDIT_WORDS = ("insufficient balance", "insufficient credits", "credit balance", "requires more credits", "recharge")


def _openai_kind(response: httpx.Response, detail: str = "") -> str:
    """OpenAI: 429 `insufficient_quota` is no credit, any other 429 a rate limit; `model_not_found` a model problem.
    The OpenAI-compatible providers (MO2 A) map the same way: OpenRouter answers 402 when the account has no credits,
    Groq and Mistral use the OpenAI status codes, Z.ai sends its own codes (above) with HTTP 429."""
    status = response.status_code
    code = _openai_error_code(response)
    words = code.split()
    low = (detail or "").lower()
    if status == 401 or "invalid_api_key" in code:
        return "auth"
    if ("insufficient_quota" in code or "billing" in code or status == 402 or any(c in words for c in _ZAI_NO_CREDIT_CODES)
            or any(w in low for w in _NO_CREDIT_WORDS)):
        return "no_credit"
    if any(c in words for c in _ZAI_RATE_CODES):
        return "rate"
    if status == 429:
        return "rate"
    if status == 404 or "model_not_found" in code or "model_decommissioned" in code:
        return "model"
    if status == 403:
        return "auth"
    if status == 400 and _mentions_image(detail):
        return "no_vision"
    return "other"


def _gemini_kind(status: int, detail: str) -> str:
    """Gemini: 429 RESOURCE_EXHAUSTED is a per-minute rate limit or a used-up quota (the text says which)."""
    low = (detail or "").lower()
    if status in (401, 403) or (status == 400 and "api key" in low):
        return "auth"
    if status == 429:
        if "per minute" in low or "perminute" in low:
            return "rate"
        if "quota" in low or "billing" in low or "credit" in low:
            return "no_credit"
        return "rate"
    if status == 404:
        return "model"
    if status == 400 and _mentions_image(low):
        return "no_vision"
    return "other"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str: ...

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 2000) -> Completion: ...


class VisionProvider(Protocol):
    """BYOK-V2: a provider that also takes one chart image (PNG) with the text - Anthropic, OpenAI and Gemini."""
    name: str
    model: str

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 2000, image_png: Optional[bytes] = None) -> Completion: ...


_ANTHROPIC_CLIENTS: Dict[Any, Any] = {}
_MAX_CLIENTS = 64


def _key_id(api_key: str) -> str:
    """Clients are cached by a digest of the key, never by the key itself."""
    return hashlib.sha256((api_key or "").encode()).hexdigest()


def _evict_oldest() -> None:
    oldest = next(iter(_ANTHROPIC_CLIENTS))
    client = _ANTHROPIC_CLIENTS.pop(oldest)
    try:
        asyncio.get_running_loop().create_task(client.close())
    except RuntimeError:
        pass


def _usage_of(response: Any) -> Completion:
    usage = getattr(response, "usage", None)
    return Completion(text="", input_tokens=int(getattr(usage, "input_tokens", 0) or 0), output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                      cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                      cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
                      stop_reason=str(getattr(response, "stop_reason", "") or ""), model=str(getattr(response, "model", "") or ""))


@dataclass
class AnthropicProvider:
    """Claude through the official `anthropic` SDK (1.x, httpx2-based). Adaptive thinking stays on (the model decides
    how much to reason); `effort` is `medium` for the strong tier and `low` for the fast tier, and the request budget
    adds the thinking headroom to the caller's text budget (C1). `fallbacks="default"` lets a safety-classifier decline
    re-run on Anthropic's recommended substitute inside the same call; a refusal that survives that is surfaced as a
    ProviderError, never as an empty strategy. `http_client` is an SDK `DefaultAsyncHttpxClient` (tests hand in one
    with a mock transport); clients are cached per key (C4)."""

    api_key: str = field(repr=False)
    model: str = field(default_factory=lambda: default_models()["anthropic"]["strong"])
    http_client: Optional[Any] = field(default=None, repr=False)
    name: str = "anthropic"
    effort: str = "medium"
    tier: str = "strong"

    def __post_init__(self) -> None:
        if self.tier in ("fast", "cheap") and self.effort == "medium":
            self.effort = EFFORT_FOR_TIER[self.tier]

    def _client(self) -> Any:
        import anthropic
        key = (_key_id(self.api_key), id(self.http_client) if self.http_client is not None else 0)
        client = _ANTHROPIC_CLIENTS.get(key)
        if client is None:
            while len(_ANTHROPIC_CLIENTS) >= _MAX_CLIENTS:
                _evict_oldest()
            client = anthropic.AsyncAnthropic(api_key=self.api_key, http_client=self.http_client, timeout=TIMEOUT, max_retries=1)
            _ANTHROPIC_CLIENTS[key] = client
        return client

    def _request(self, system: str, user: str, budget: int, image_png: Optional[bytes] = None) -> dict:
        content: Any = user
        if image_png is not None:
            # BYOK-V2: one chart image (base64 PNG) before the text, as the Messages API takes it.
            content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(image_png)}},
                       {"type": "text", "text": user}]
        body: dict = {"model": self.model, "max_tokens": budget,
                      # C4: the system prompt is the static part - cacheable; the user text changes every call.
                      "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                      "messages": [{"role": "user", "content": content}],
                      "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        if _supports_effort(self.model):
            body["thinking"] = {"type": "adaptive"}
            body["output_config"] = {"effort": self.effort}
        return body

    def _budget(self, max_tokens: int) -> int:
        return int(max_tokens) + (thinking_headroom(self.effort) if _supports_effort(self.model) else 0)

    async def complete(self, system: str, user: str, *, max_tokens: int = 16000) -> str:
        return (await self.complete_full(system, user, max_tokens=max_tokens)).text

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 16000, image_png: Optional[bytes] = None) -> Completion:
        import anthropic
        client = self._client()
        budget = self._budget(max_tokens)
        spent = Completion(text="", model=self.model, provider=self.name)
        for attempt in range(2):
            try:
                response = await client.with_options(timeout=timeout_for(budget)).beta.messages.create(**self._request(system, user, budget, image_png))
            except anthropic.AuthenticationError as exc:
                raise ProviderError("Anthropic rejected the API key (401) - re-enter it under Settings > AI", usage=spent, kind="auth") from exc
            except anthropic.RateLimitError as exc:
                raise ProviderError("Anthropic rate limit (429) - try again shortly", usage=spent, kind="rate") from exc
            except anthropic.NotFoundError as exc:
                raise ProviderError(f"Anthropic does not know model '{self.model}' - pick another under Settings > AI", usage=spent, kind="model") from exc
            except anthropic.APIStatusError as exc:
                raise ProviderError(f"Anthropic error HTTP {exc.status_code}: {exc.type or exc.message}", usage=spent,
                                    kind=_anthropic_status_kind(exc)) from exc
            except anthropic.APIConnectionError as exc:
                raise ProviderError(f"Anthropic unreachable: {exc.__class__.__name__}", usage=spent, kind="unreachable") from exc
            result = _usage_of(response)
            spent.add_usage(result)
            if response.stop_reason == "refusal":
                detail = getattr(getattr(response, "stop_details", None), "explanation", None) or "the request was declined by a safety classifier"
                raise ProviderError(f"Anthropic declined the request: {detail}", usage=spent)
            text = "".join(block.text for block in response.content if block.type == "text")
            if response.stop_reason == "max_tokens":
                # C1: cut off at the limit - never shown as complete; one retry with twice the budget.
                logger.warning("Anthropic answer cut off at %s tokens (model %s, attempt %s)", budget, self.model, attempt + 1)
                budget *= 2
                continue
            if not text:
                raise ProviderError("Anthropic answered without text content", usage=spent)
            spent.text, spent.stop_reason, spent.model = text, str(response.stop_reason or "end_turn"), str(getattr(response, "model", "") or self.model)
            return spent
        raise ProviderError(f"Anthropic's answer was cut off at the token limit twice (model {self.model}); nothing partial is shown", usage=spent)


    async def complete_tools(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], *,
                             max_tokens: int = 4000) -> ToolTurn:
        """H-C2 (ADR-0019): one model step with our tool specs (provider-native tool use; our code runs the loop).
        `messages` is the Anthropic message list; the caller appends `assistant_content` and the tool results."""
        import anthropic
        client = self._client()
        budget = self._budget(max_tokens)
        body = self._request(system, "", budget)
        body["messages"] = messages
        body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools]
        try:
            response = await client.with_options(timeout=timeout_for(budget)).beta.messages.create(**body)
        except anthropic.AuthenticationError as exc:
            raise ProviderError("Anthropic rejected the API key (401) - re-enter it under Settings > AI", kind="auth") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("Anthropic rate limit (429) - try again shortly", kind="rate") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Anthropic error HTTP {exc.status_code}: {exc.type or exc.message}", kind=_anthropic_status_kind(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"Anthropic unreachable: {exc.__class__.__name__}", kind="unreachable") from exc
        usage = _usage_of(response)
        usage.model, usage.provider = str(getattr(response, "model", "") or self.model), self.name
        if response.stop_reason == "refusal":
            detail = getattr(getattr(response, "stop_details", None), "explanation", None) or "the request was declined by a safety classifier"
            raise ProviderError(f"Anthropic declined the request: {detail}", usage=usage)
        if response.stop_reason == "max_tokens":
            raise ProviderError(f"Anthropic's step was cut off at the token limit (model {self.model})", usage=usage)
        text = "".join(block.text for block in response.content if block.type == "text")
        calls = [ToolCall(block.id, block.name, dict(block.input or {})) for block in response.content if block.type == "tool_use"]
        usage.text, usage.stop_reason = text, str(response.stop_reason or "end_turn")
        return ToolTurn(text, calls, response.content, usage, usage.stop_reason)


_OPENAI_CLIENT: Optional[httpx.AsyncClient] = None


def _openai_client() -> httpx.AsyncClient:
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        _OPENAI_CLIENT = httpx.AsyncClient(timeout=TIMEOUT)
    return _OPENAI_CLIENT


@dataclass
class OpenAIProvider:
    """OpenAI Chat Completions - and, with another `base_url` / `chat_path` (MO2 A, `OPENAI_COMPATIBLE`), the providers
    that speak the same API: OpenRouter, Groq, Mistral and Z.ai. `max_tokens_field` is the budget parameter the provider
    documents, `image_as_string` sends the chart as a plain data-URL string (Mistral's documented form) and `headroom`
    adds the low-effort thinking headroom for providers whose models may reason before answering (C1)."""
    api_key: str = field(repr=False)
    model: str = field(default_factory=lambda: default_models()["openai"]["strong"])
    client: Optional[httpx.AsyncClient] = field(default=None, repr=False)
    name: str = "openai"
    base_url: str = "https://api.openai.com"
    tier: str = "strong"
    chat_path: str = "/v1/chat/completions"
    max_tokens_field: str = "max_completion_tokens"
    image_as_string: bool = False
    headroom: bool = False

    @property
    def _url(self) -> str:
        return f"{self.base_url}{self.chat_path}"

    @property
    def _free_model(self) -> bool:
        """OpenRouter's free models (`:free`, the Free Models Router): the call is recorded under the model asked for,
        so it is priced at zero even when the answer names the free model the router picked."""
        m = (self.model or "").lower()
        return m.endswith(":free") or m == "openrouter/free"

    def _payload(self, system: str, user: str, budget: int, image_png: Optional[bytes] = None) -> dict:
        # C3: `max_completion_tokens` is the current parameter for every model; reasoning models reject `temperature`
        # and take `reasoning_effort` instead (low for the fast tier, like the Anthropic effort).
        content: Any = user
        if image_png is not None:
            # BYOK-V2: the chart as a data URL (low detail is enough for a candle chart and costs a fixed few tokens).
            data_url = f"data:image/png;base64,{_b64(image_png)}"
            content = [{"type": "text", "text": user},
                       {"type": "image_url", "image_url": data_url if self.image_as_string else {"url": data_url, "detail": "low"}}]
        payload: dict = {"model": self.model, self.max_tokens_field: budget,
                         "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        if is_reasoning_model(self.model):
            payload["reasoning_effort"] = _effort_for(self.tier)
        else:
            payload["temperature"] = 0.2
        return payload

    def _budget(self, max_tokens: int) -> int:
        # C1 applies here too: on reasoning models `max_completion_tokens` caps the reasoning plus the visible text.
        reasons = is_reasoning_model(self.model) or self.headroom
        return int(max_tokens) + (thinking_headroom(_effort_for(self.tier)) if reasons else 0)

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        return (await self.complete_full(system, user, max_tokens=max_tokens)).text

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 2000, image_png: Optional[bytes] = None) -> Completion:
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        client = self.client or _openai_client()
        budget = self._budget(max_tokens)
        spent = Completion(text="", model=self.model, provider=self.name)
        for attempt in range(2):
            try:
                response = await client.post(self._url, json=self._payload(system, user, budget, image_png), headers=headers,
                                             timeout=timeout_for(budget))
            except httpx.HTTPError as exc:
                raise ProviderError(f"Provider unreachable: {exc.__class__.__name__}", usage=spent, kind="unreachable") from exc
            if response.status_code == 401:
                raise ProviderError("Provider rejected the API key (401) - re-enter it under Settings > AI", usage=spent, kind="auth")
            if response.status_code == 429:
                raise ProviderError("Provider rate limit (429) - try again shortly", usage=spent,
                                    kind=_openai_kind(response, _error_detail(response, self.api_key)))
            if response.status_code >= 400:
                detail = _error_detail(response, self.api_key)
                # C3: the body says which parameter the model rejected; the key is in the header, never in the body.
                logger.warning("OpenAI HTTP %s for model %s: %s", response.status_code, self.model, detail)
                raise ProviderError(f"Provider error HTTP {response.status_code}: {detail}" if detail else f"Provider error HTTP {response.status_code}",
                                    usage=spent, kind=_openai_kind(response, detail))
            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderError("Provider answered with non-JSON", usage=spent) from exc
            usage = body.get("usage") or {}
            spent.add_usage(Completion(text="", input_tokens=int(usage.get("prompt_tokens") or 0), output_tokens=int(usage.get("completion_tokens") or 0),
                                       cache_read_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)))
            try:
                choice = body["choices"][0]
                text = choice["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise ProviderError("OpenAI answered without a message", usage=spent) from exc
            if choice.get("finish_reason") == "length":
                logger.warning("OpenAI answer cut off at %s tokens (model %s, attempt %s)", budget, self.model, attempt + 1)
                budget *= 2
                continue
            if not text:
                raise ProviderError("OpenAI answered without text content", usage=spent)
            answered = self.model if self._free_model else str(body.get("model") or self.model)
            spent.text, spent.stop_reason, spent.model = text, str(choice.get("finish_reason") or "stop"), answered
            return spent
        raise ProviderError(f"OpenAI's answer was cut off at the token limit twice (model {self.model}); nothing partial is shown", usage=spent)

    @staticmethod
    def _chat_messages(system: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """H-C2b: the agent loop's message list (Anthropic-shaped: user text, assistant blocks, user tool_result blocks)
        as Chat Completions messages (assistant tool_calls, one `tool` message per result)."""
        out: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                out.append({"role": m["role"], "content": content})
                continue
            blocks = [b if isinstance(b, dict) else b.model_dump() for b in (content or [])]
            if m["role"] == "assistant":
                text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                         for b in blocks if b.get("type") == "tool_use"]
                msg: Dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            else:
                for b in blocks:
                    if b.get("type") == "tool_result":
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": str(b.get("content") or "")})
                    elif b.get("type") == "text":
                        out.append({"role": "user", "content": b.get("text", "")})
        return out

    async def complete_tools(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], *,
                             max_tokens: int = 4000) -> ToolTurn:
        """H-C2b (ADR-0019 §6): one model step with function tools on Chat Completions. The assistant content handed back
        is Anthropic-shaped blocks, so the loop stays provider-neutral; nothing hidden is dropped (Chat Completions
        returns no reasoning items to send back)."""
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        client = self.client or _openai_client()
        budget = self._budget(max_tokens)
        payload = self._payload(system, "", budget)
        payload["messages"] = self._chat_messages(system, messages)
        payload["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
                            for t in tools]
        try:
            response = await client.post(self._url, json=payload, headers=headers, timeout=timeout_for(budget))
        except httpx.HTTPError as exc:
            raise ProviderError(f"Provider unreachable: {exc.__class__.__name__}", kind="unreachable") from exc
        if response.status_code == 401:
            raise ProviderError("Provider rejected the API key (401) - re-enter it under Settings > AI", kind="auth")
        if response.status_code == 429:
            raise ProviderError("Provider rate limit (429) - try again shortly", kind=_openai_kind(response, _error_detail(response, self.api_key)))
        if response.status_code >= 400:
            detail = _error_detail(response, self.api_key)
            raise ProviderError(f"Provider error HTTP {response.status_code}: {detail}" if detail else f"Provider error HTTP {response.status_code}",
                                kind=_openai_kind(response, detail))
        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("OpenAI answered without a message") from exc
        raw = body.get("usage") or {}
        usage = Completion(text="", input_tokens=int(raw.get("prompt_tokens") or 0), output_tokens=int(raw.get("completion_tokens") or 0),
                           cache_read_tokens=int((raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                           model=self.model if self._free_model else str(body.get("model") or self.model), provider=self.name)
        if choice.get("finish_reason") == "length":
            raise ProviderError(f"OpenAI's step was cut off at the token limit (model {self.model})", usage=usage)
        text = message.get("content") or ""
        calls: List[ToolCall] = []
        for c in message.get("tool_calls") or []:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {"__invalid_json__": (fn.get("arguments") or "")[:200]}      # refused by the tool's input model
            calls.append(ToolCall(str(c.get("id")), str(fn.get("name")), args if isinstance(args, dict) else {"__invalid_json__": str(args)[:200]}))
        blocks: List[Dict[str, Any]] = ([{"type": "text", "text": text}] if text else []) + \
            [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in calls]
        stop = "tool_use" if calls else "end_turn"
        usage.text, usage.stop_reason = text, stop
        return ToolTurn(text, calls, blocks, usage, stop)


def _error_detail(response: httpx.Response, secret: Optional[str] = None) -> str:
    """The provider's error message (max 300 chars). `secret` (the key of the call) is masked should a provider ever echo it."""
    try:
        body = response.json()
    except ValueError:
        text = response.text or ""
    else:
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            text = str(err.get("message") or err.get("type") or "")
        else:
            text = json.dumps(body) if body else ""
    if secret:
        text = text.replace(secret, "[redacted]")
    return text[:300]


_GEMINI_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")      # goes into the URL path: no slashes, no leading dot


@dataclass
class GeminiProvider:
    """BYOK-V1: Google Gemini through the Generative Language REST API (`models/{model}:generateContent`). Only used with a
    user's own key ("Vision with your own AI key"); the tenant provider stays Anthropic / OpenAI / rule-based. The key
    travels in the `x-goog-api-key` header - never in the URL, so it cannot end up in an access or client log line."""

    api_key: str = field(repr=False)
    model: str = "gemini-2.5-flash"
    client: Optional[httpx.AsyncClient] = field(default=None, repr=False)
    name: str = "gemini"
    base_url: str = "https://generativelanguage.googleapis.com"
    tier: str = "cheap"

    def _payload(self, system: str, user: str, budget: int, image_png: Optional[bytes] = None) -> dict:
        parts: List[Dict[str, Any]] = [{"text": user}]
        if image_png is not None:
            # BYOK-V2: the chart inline (base64 PNG) before the text.
            parts = [{"inlineData": {"mimeType": "image/png", "data": _b64(image_png)}}, {"text": user}]
        return {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"maxOutputTokens": budget, "temperature": 0.2}}

    def _budget(self, max_tokens: int) -> int:
        # Thinking models (2.5 generation) spend output tokens on thoughts first (C1, like the other providers).
        return int(max_tokens) + thinking_headroom(_effort_for(self.tier))

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        return (await self.complete_full(system, user, max_tokens=max_tokens)).text

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 2000, image_png: Optional[bytes] = None) -> Completion:
        if not _GEMINI_MODEL_ID.match(self.model or ""):
            raise ProviderError("Gemini model id is not valid - pick another under Settings > AI", kind="model")
        headers = {"x-goog-api-key": self.api_key, "content-type": "application/json"}
        client = self.client or _openai_client()
        budget = self._budget(max_tokens)
        spent = Completion(text="", model=self.model, provider=self.name)
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        for attempt in range(2):
            try:
                response = await client.post(url, json=self._payload(system, user, budget, image_png), headers=headers, timeout=timeout_for(budget))
            except httpx.HTTPError as exc:
                raise ProviderError(f"Gemini unreachable: {exc.__class__.__name__}", usage=spent, kind="unreachable") from exc
            detail = _error_detail(response, self.api_key) if response.status_code >= 400 else ""
            kind = _gemini_kind(response.status_code, detail) if response.status_code >= 400 else "other"
            if response.status_code in (401, 403) or (response.status_code == 400 and "api key" in detail.lower()):
                raise ProviderError(f"Gemini rejected the API key (HTTP {response.status_code}) - re-enter it under Settings > AI", usage=spent, kind=kind)
            if response.status_code == 429:
                raise ProviderError("Gemini rate limit or quota (429) - try again later", usage=spent, kind=kind)
            if response.status_code == 404:
                raise ProviderError(f"Gemini does not know model '{self.model}' - pick another under Settings > AI", usage=spent, kind=kind)
            if response.status_code >= 400:
                raise ProviderError(f"Gemini error HTTP {response.status_code}: {detail}" if detail else f"Gemini error HTTP {response.status_code}",
                                    usage=spent, kind=kind)
            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderError("Gemini answered with non-JSON", usage=spent) from exc
            meta = (body.get("usageMetadata") or {}) if isinstance(body, dict) else {}
            cached = int(meta.get("cachedContentTokenCount") or 0)
            spent.add_usage(Completion(text="", input_tokens=max(0, int(meta.get("promptTokenCount") or 0) - cached), cache_read_tokens=cached,
                                       output_tokens=int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0)))
            try:
                candidate = body["candidates"][0]
            except (KeyError, IndexError, TypeError) as exc:
                reason = (body.get("promptFeedback") or {}).get("blockReason") if isinstance(body, dict) else None
                raise ProviderError(f"Gemini declined the request: {reason}" if reason else "Gemini answered without a candidate", usage=spent) from exc
            finish = str(candidate.get("finishReason") or "STOP")
            parts = (candidate.get("content") or {}).get("parts") or []
            text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and not p.get("thought"))
            if finish == "MAX_TOKENS":
                logger.warning("Gemini answer cut off at %s tokens (model %s, attempt %s)", budget, self.model, attempt + 1)
                budget *= 2
                continue
            if finish in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
                raise ProviderError(f"Gemini declined the request ({finish})", usage=spent)
            if not text:
                raise ProviderError("Gemini answered without text content", usage=spent)
            spent.text, spent.stop_reason, spent.model = text, finish, str(body.get("modelVersion") or self.model)
            return spent
        raise ProviderError(f"Gemini's answer was cut off at the token limit twice (model {self.model}); nothing partial is shown", usage=spent)


# BYOK-V1: the providers a user may bring their own key for on the "Vision with your own AI key" card (Settings > AI,
# without `ai_one_key`). Unchanged by MO2 A, so that card behaves as before.
BYOK_PROVIDERS = ("anthropic", "openai", "gemini")

# Master Order 2 part A: the OpenAI-compatible providers the Connect AI panel also takes (behind `ai_one_key`). Each one
# is the existing OpenAIProvider with its own base URL, path and budget parameter. Checked on 2026-10-22. The provider
# websites could not be opened directly from the build machine that day, so each shape was read from the provider's own
# published source (its official SDK or docs repository) or, where only the website has it, from a search restricted to
# the provider's own docs domain:
# - OpenRouter: POST https://openrouter.ai/api/v1/chat/completions, `Authorization: Bearer`, `max_completion_tokens`
#   (`max_tokens` deprecated), images as `image_url` parts; 401 / 402 (no credits) / 429. Source: the official SDK
#   github.com/OpenRouterTeam/python-sdk (src/openrouter/chat.py, sdkconfiguration.py, docs/sdks/chat/README.mdx) and
#   openrouter.ai/docs/guides/routing/routers/free-router (search; the `openrouter/free` router reads images too).
# - Groq: POST https://api.groq.com/openai/v1/chat/completions, `Authorization: Bearer`, `max_completion_tokens`
#   (`max_tokens` deprecated), `reasoning_effort` for gpt-oss. Source: the official SDK github.com/groq/groq-python
#   (src/groq/_client.py, src/groq/resources/chat/completions.py). Text only here: its vision models change often
#   (console.groq.com/docs/deprecations), so ATP does not send it charts.
# - Mistral: POST https://api.mistral.ai/v1/chat/completions, `Authorization: Bearer`, `max_tokens`, images as
#   `{"type": "image_url", "image_url": "data:image/...;base64,..."}`; Large 3 / Medium 3.1 / Small 3.2 read images.
#   Source: Mistral's docs source github.com/mistralai/platform-docs-public (quickstarts/studio/
#   activate-and-generate-api-key, studio/conversations/vision) and github.com/mistralai/client-python.
# - Z.ai GLM: POST https://api.z.ai/api/paas/v4/chat/completions, `Authorization: Bearer`, `max_tokens`; error codes
#   1113 (no balance) and 1302 (rate limit) with HTTP 429; GLM-4.5-Flash is free. Source: docs.z.ai (search;
#   api-reference/llm/chat-completion, api-reference/api-code, guides/llm/glm-4.5). Text only here: its vision models
#   (GLM-4.6V, GLM-5V-Turbo, github.com/zai-org/GLM-V) were not checked against the API reference.
# Cloudflare Workers AI is not here yet: its OpenAI-compatible URL needs the account id next to the token
# (developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility), which one paste cannot carry.
OPENAI_COMPATIBLE: Dict[str, Dict[str, Any]] = {
    "openrouter": {"base_url": "https://openrouter.ai", "chat_path": "/api/v1/chat/completions",
                   "max_tokens_field": "max_completion_tokens", "image_as_string": False, "headroom": True},
    "groq": {"base_url": "https://api.groq.com", "chat_path": "/openai/v1/chat/completions",
             "max_tokens_field": "max_completion_tokens", "image_as_string": False, "headroom": True},
    "mistral": {"base_url": "https://api.mistral.ai", "chat_path": "/v1/chat/completions",
                "max_tokens_field": "max_tokens", "image_as_string": True, "headroom": False},
    "zai": {"base_url": "https://api.z.ai", "chat_path": "/api/paas/v4/chat/completions",
            "max_tokens_field": "max_tokens", "image_as_string": False, "headroom": True},
}
# Every provider a user's own key can be for under "Connect AI in one paste" (`ai_one_key`), free Google Gemini first.
CONNECT_PROVIDERS = ("gemini", "anthropic", "openai", "openrouter", "groq", "mistral", "zai")

# The BYOK providers that read chart images. A text-only provider (Groq, Z.ai) is never sent a chart: the vision
# features refuse it with the plain "cannot read charts" sentence.
VISION_PROVIDERS = ("anthropic", "openai", "gemini", "openrouter", "mistral")
# The models of each vision provider that read images (its tier models above). A vision call on a model outside its
# list is moved to the provider's first one.
VISION_MODELS: Dict[str, Any] = {"mistral": ("mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"),
                                 "openrouter": ("openrouter/free", "openrouter/auto")}


# A provider whose default models are free ones that may train on what they are sent (OpenRouter's Free Models Router):
# once the user confirms the key is on a paid plan, the calls go to its paid router instead
# (`AI_OPENROUTER_PAID_MODEL` overrides it), so "paid plan" really means paid models - ATP's private method is never
# sent to a free model.
PAID_MODELS: Dict[str, str] = {"openrouter": "openrouter/auto"}


def paid_model(provider: str) -> Optional[str]:
    default = PAID_MODELS.get(provider)
    if default is None:
        return None
    return str(os.environ.get(f"AI_{provider.upper()}_PAID_MODEL") or default).strip()


def vision_model(provider: str, model: str) -> str:
    """The model a chart goes to: `model` when the provider's vision list has it (or has no list), else its first one."""
    allowed = VISION_MODELS.get(provider)
    if not allowed or model in allowed:
        return model
    return str(os.environ.get(f"AI_{provider.upper()}_VISION_MODEL") or allowed[0]).strip()


def build_byok_provider(name: str, api_key: str, model: str, client: Optional[Any] = None, *, task: Optional[str] = None,
                        vision: bool = False, paid: bool = False) -> VisionProvider:
    """BYOK-V1: one user's provider, on that user's key and chosen model, at the cheap tier (low effort). `client` is the
    Anthropic SDK http client or an `httpx.AsyncClient` (tests hand in mock transports). Never falls back to another key.
    Settings redesign A (R3): with `task`, the model and effort follow the task's tier (TASK_TIERS, the same tiers as the
    organisation's key; Gemini has its own tier models) and `model` is ignored - a user never picks a model.
    MO2 A: the OpenAI-compatible providers (OPENAI_COMPATIBLE) run on OpenAIProvider with their own URL; an empty
    `model` takes the tier's model; `vision=True` refuses a text-only provider (kind `no_vision`) and keeps the call on
    a model that reads images; `paid=True` (the user confirmed a paid plan) moves a free-model provider to its paid
    router (PAID_MODELS)."""
    name = (name or "").lower()
    if not api_key:
        raise ProviderError("No API key stored for this provider", kind="auth")
    if vision and name not in VISION_PROVIDERS:
        raise ProviderError("This provider cannot read chart images", kind="no_vision")
    tier = "cheap"
    if task is not None:
        tier = TASK_TIERS.get(task, "strong")
        model = default_models().get(name, {}).get(tier) or model
    model = model or default_models().get(name, {}).get(tier, "")
    if paid and paid_model(name):
        model = str(paid_model(name))
    if vision:
        model = vision_model(name, model)
    if name == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model, http_client=client, effort=EFFORT_FOR_TIER[tier], tier=tier)
    if name == "openai":
        return OpenAIProvider(api_key=api_key, model=model, client=client, tier=tier)
    if name == "gemini":
        return GeminiProvider(api_key=api_key, model=model, client=client, tier=tier)
    spec = OPENAI_COMPATIBLE.get(name)
    if spec is not None:
        return OpenAIProvider(api_key=api_key, model=model, client=client, name=name, tier=tier, **spec)
    raise ProviderError(f"Unknown provider '{name}'; one of {CONNECT_PROVIDERS}")


@dataclass
class RuleBasedProvider:
    """No network, no key: the same deterministic parser behind the Strategy Builder's
    "describe in plain English" box, wrapped to answer in the generator's JSON contract. `reason` says why the
    tenant got the rules instead of its configured provider (budget spent, provider off) - shown to the user."""

    model: str = RULE_BASED_MODEL
    name: str = "rule_based"
    reason: str = ""

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        from app.strategy_engine.nlu_parser import parse_strategy_description
        text = user.split("USER REQUEST:", 1)[-1].strip() if "USER REQUEST:" in user else user
        result = parse_strategy_description(text, name="AI draft")
        return json.dumps({
            "config": result.config.model_dump(),
            "explanation": f"Deterministic parse of the request ({self.reason or 'no external model configured'}): " + "; ".join(result.interpreted or ["no indicator rules recognised"]),
            "warnings": list(result.warnings),
        })

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 2000) -> Completion:
        return Completion(text=await self.complete(system, user, max_tokens=max_tokens), model=self.model, provider=self.name)


def build_provider(name: str, api_key: Optional[str], model: Optional[str], client: Optional[Any] = None, *, task: str = "general") -> LLMProvider:
    """`client` is an SDK http client for Anthropic (httpx2-based) or an `httpx.AsyncClient` for OpenAI. `task` picks
    the tier (C2): the fast tier runs the operator's fast model at low effort; `model` is the tenant's generation model."""
    name = (name or "rule_based").lower()
    tier = TASK_TIERS.get(task, "strong")
    if name == "anthropic":
        if not api_key:
            raise ProviderError("Anthropic needs an API key")
        return AnthropicProvider(api_key=api_key, model=model_for(name, model, task), http_client=client, effort=EFFORT_FOR_TIER[tier], tier=tier)
    if name == "openai":
        if not api_key:
            raise ProviderError("OpenAI needs an API key")
        return OpenAIProvider(api_key=api_key, model=model_for(name, model, task), client=client, tier=tier)
    if name == "rule_based":
        return RuleBasedProvider()
    raise ProviderError(f"Unknown provider '{name}'; one of {PROVIDERS}")

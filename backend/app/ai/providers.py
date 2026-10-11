"""Phase L1: the LLM provider seam (master prompt section 56, V4.3) - P0.8-C provider layer.

One small interface - `complete(system, user) -> str` plus `complete_full(...) -> Completion` with the token counts -
and three implementations: Anthropic (Messages API through the official SDK), OpenAI (Chat Completions) and a
deterministic **rule-based** provider that needs no key and no network (it runs the existing NLU parser), so every AI
feature degrades to something explainable when no provider is configured. Keys live per tenant in
`ai_provider_configs`, encrypted at rest, entered on the Settings page only; nothing here reads them from the
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
}
EFFORT_FOR_TIER = {"strong": "medium", "fast": "low", "cheap": "low"}
# C1: thinking headroom per effort level (tokens the model may spend reasoning before the text starts).
THINKING_HEADROOM = {"low": 2000, "medium": 6000, "high": 12000, "xhigh": 24000, "max": 32000}
_FALLBACK_MODELS = {"anthropic": {"strong": "claude-opus-5-5", "fast": "claude-sonnet-5-5", "cheap": "claude-haiku-5-5"},
                    "openai": {"strong": "gpt-4.1", "fast": "gpt-4.1-mini", "cheap": "gpt-4.1-nano"},
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
    """C3: OpenAI reasoning models reject `temperature` and only take `max_completion_tokens`."""
    m = (model or "").lower()
    return m.startswith(("o1", "o3", "o4", "gpt-5"))


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


class ProviderError(RuntimeError):
    """A provider failure the caller shows as "AI unavailable" and answers from the rules. `usage` carries the tokens
    of the failed attempts when the provider reported them (they are billed)."""

    def __init__(self, message: str, *, usage: Optional[Completion] = None) -> None:
        super().__init__(message)
        self.usage = usage


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str: ...

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 2000) -> Completion: ...


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

    def _request(self, system: str, user: str, budget: int) -> dict:
        body: dict = {"model": self.model, "max_tokens": budget,
                      # C4: the system prompt is the static part - cacheable; the user text changes every call.
                      "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                      "messages": [{"role": "user", "content": user}],
                      "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        if _supports_effort(self.model):
            body["thinking"] = {"type": "adaptive"}
            body["output_config"] = {"effort": self.effort}
        return body

    def _budget(self, max_tokens: int) -> int:
        return int(max_tokens) + (thinking_headroom(self.effort) if _supports_effort(self.model) else 0)

    async def complete(self, system: str, user: str, *, max_tokens: int = 16000) -> str:
        return (await self.complete_full(system, user, max_tokens=max_tokens)).text

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 16000) -> Completion:
        import anthropic
        client = self._client()
        budget = self._budget(max_tokens)
        spent = Completion(text="", model=self.model, provider=self.name)
        for attempt in range(2):
            try:
                response = await client.with_options(timeout=timeout_for(budget)).beta.messages.create(**self._request(system, user, budget))
            except anthropic.AuthenticationError as exc:
                raise ProviderError("Anthropic rejected the API key (401) - re-enter it under Settings", usage=spent) from exc
            except anthropic.RateLimitError as exc:
                raise ProviderError("Anthropic rate limit (429) - try again shortly", usage=spent) from exc
            except anthropic.NotFoundError as exc:
                raise ProviderError(f"Anthropic does not know model '{self.model}' - pick another under Settings", usage=spent) from exc
            except anthropic.APIStatusError as exc:
                raise ProviderError(f"Anthropic error HTTP {exc.status_code}: {exc.type or exc.message}", usage=spent) from exc
            except anthropic.APIConnectionError as exc:
                raise ProviderError(f"Anthropic unreachable: {exc.__class__.__name__}", usage=spent) from exc
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
            raise ProviderError("Anthropic rejected the API key (401) - re-enter it under Settings") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("Anthropic rate limit (429) - try again shortly") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Anthropic error HTTP {exc.status_code}: {exc.type or exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"Anthropic unreachable: {exc.__class__.__name__}") from exc
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
    api_key: str = field(repr=False)
    model: str = field(default_factory=lambda: default_models()["openai"]["strong"])
    client: Optional[httpx.AsyncClient] = field(default=None, repr=False)
    name: str = "openai"
    base_url: str = "https://api.openai.com"
    tier: str = "strong"

    def _payload(self, system: str, user: str, budget: int) -> dict:
        # C3: `max_completion_tokens` is the current parameter for every model; reasoning models reject `temperature`
        # and take `reasoning_effort` instead (low for the fast tier, like the Anthropic effort).
        payload: dict = {"model": self.model, "max_completion_tokens": budget,
                         "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if is_reasoning_model(self.model):
            payload["reasoning_effort"] = _effort_for(self.tier)
        else:
            payload["temperature"] = 0.2
        return payload

    def _budget(self, max_tokens: int) -> int:
        # C1 applies here too: on reasoning models `max_completion_tokens` caps the reasoning plus the visible text.
        return int(max_tokens) + (thinking_headroom(_effort_for(self.tier)) if is_reasoning_model(self.model) else 0)

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        return (await self.complete_full(system, user, max_tokens=max_tokens)).text

    async def complete_full(self, system: str, user: str, *, max_tokens: int = 2000) -> Completion:
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        client = self.client or _openai_client()
        budget = self._budget(max_tokens)
        spent = Completion(text="", model=self.model, provider=self.name)
        for attempt in range(2):
            try:
                response = await client.post(f"{self.base_url}/v1/chat/completions", json=self._payload(system, user, budget), headers=headers,
                                             timeout=timeout_for(budget))
            except httpx.HTTPError as exc:
                raise ProviderError(f"Provider unreachable: {exc.__class__.__name__}", usage=spent) from exc
            if response.status_code == 401:
                raise ProviderError("Provider rejected the API key (401) - re-enter it under Settings", usage=spent)
            if response.status_code == 429:
                raise ProviderError("Provider rate limit (429) - try again shortly", usage=spent)
            if response.status_code >= 400:
                detail = _error_detail(response)
                # C3: the body says which parameter the model rejected; the key is in the header, never in the body.
                logger.warning("OpenAI HTTP %s for model %s: %s", response.status_code, self.model, detail)
                raise ProviderError(f"Provider error HTTP {response.status_code}: {detail}" if detail else f"Provider error HTTP {response.status_code}", usage=spent)
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
            spent.text, spent.stop_reason, spent.model = text, str(choice.get("finish_reason") or "stop"), str(body.get("model") or self.model)
            return spent
        raise ProviderError(f"OpenAI's answer was cut off at the token limit twice (model {self.model}); nothing partial is shown", usage=spent)


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return (response.text or "")[:300]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or "")[:300]
    return json.dumps(body)[:300] if body else ""


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

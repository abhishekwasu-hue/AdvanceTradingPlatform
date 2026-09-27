"""Phase L1: the LLM provider seam (master prompt section 56, V4.3).

One small interface - `complete(system, user) -> str` - with three implementations: Anthropic
(Messages API), OpenAI (Chat Completions) and a deterministic **rule-based** provider that
needs no key and no network (it runs the existing NLU parser), so every AI feature degrades to
something explainable when no provider is configured. Keys live per tenant in
`ai_provider_configs`, encrypted at rest, entered on the Settings page only; nothing here reads
them from the environment or logs them. The AI never sees broker credentials (safety rule 15):
a provider gets text in and text out, nothing else.
"""
import json
from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

from app.core import config

DEFAULT_MODELS = {"anthropic": "claude-sonnet-4-5", "openai": "gpt-4o-mini", "rule_based": "nlu-parser-v1"}
PROVIDERS = tuple(DEFAULT_MODELS)
TIMEOUT = float(getattr(config, "AI_PROVIDER_TIMEOUT_SECONDS", 45.0))


class ProviderError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str: ...


@dataclass
class AnthropicProvider:
    api_key: str
    model: str = DEFAULT_MODELS["anthropic"]
    client: Optional[httpx.AsyncClient] = None
    name: str = "anthropic"
    base_url: str = "https://api.anthropic.com"

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        payload = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        return await _post(self.client, f"{self.base_url}/v1/messages", payload, headers, _anthropic_text)


@dataclass
class OpenAIProvider:
    api_key: str
    model: str = DEFAULT_MODELS["openai"]
    client: Optional[httpx.AsyncClient] = None
    name: str = "openai"
    base_url: str = "https://api.openai.com"

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        payload = {"model": self.model, "max_tokens": max_tokens, "temperature": 0.2,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        return await _post(self.client, f"{self.base_url}/v1/chat/completions", payload, headers, _openai_text)


@dataclass
class RuleBasedProvider:
    """No network, no key: the same deterministic parser behind the Strategy Builder's
    "describe in plain English" box, wrapped to answer in the generator's JSON contract."""

    model: str = DEFAULT_MODELS["rule_based"]
    name: str = "rule_based"

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        from app.strategy_engine.nlu_parser import parse_strategy_description
        text = user.split("USER REQUEST:", 1)[-1].strip() if "USER REQUEST:" in user else user
        result = parse_strategy_description(text, name="AI draft")
        return json.dumps({
            "config": result.config.model_dump(),
            "explanation": "Deterministic parse of the request (no external model configured): " + "; ".join(result.interpreted or ["no indicator rules recognised"]),
            "warnings": list(result.warnings),
        })


def _anthropic_text(body: dict) -> str:
    parts = body.get("content") or []
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    if not text:
        raise ProviderError("Anthropic answered without text content")
    return text


def _openai_text(body: dict) -> str:
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("OpenAI answered without a message") from exc


async def _post(client: Optional[httpx.AsyncClient], url: str, payload: dict, headers: dict, extract) -> str:
    owns = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Provider unreachable: {exc.__class__.__name__}") from exc
    finally:
        if owns:
            await client.aclose()
    if response.status_code == 401:
        raise ProviderError("Provider rejected the API key (401) - re-enter it under Settings")
    if response.status_code == 429:
        raise ProviderError("Provider rate limit (429) - try again shortly")
    if response.status_code >= 400:
        raise ProviderError(f"Provider error HTTP {response.status_code}")
    try:
        return extract(response.json())
    except ValueError as exc:
        raise ProviderError("Provider answered with non-JSON") from exc


def build_provider(name: str, api_key: Optional[str], model: Optional[str], client: Optional[httpx.AsyncClient] = None) -> LLMProvider:
    name = (name or "rule_based").lower()
    if name == "anthropic":
        if not api_key:
            raise ProviderError("Anthropic needs an API key")
        return AnthropicProvider(api_key=api_key, model=model or DEFAULT_MODELS[name], client=client)
    if name == "openai":
        if not api_key:
            raise ProviderError("OpenAI needs an API key")
        return OpenAIProvider(api_key=api_key, model=model or DEFAULT_MODELS[name], client=client)
    if name == "rule_based":
        return RuleBasedProvider()
    raise ProviderError(f"Unknown provider '{name}'; one of {PROVIDERS}")

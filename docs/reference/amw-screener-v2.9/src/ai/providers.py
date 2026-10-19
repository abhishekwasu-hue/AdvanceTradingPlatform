"""OpenAI and Anthropic adapters. Every call receives the REQUESTING USER's key explicitly.

There is deliberately no code path that reads a provider key from the environment or from a company account.
HTTP goes through an injectable `transport` (tests use a mock; production uses `requests`).
Errors are classified into stable codes and messages are redacted.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from .redact import redact

OPENAI_BASE = "https://api.openai.com/v1"
ANTHROPIC_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
PROVIDERS = ("openai", "anthropic")
LABEL = {"openai": "OpenAI", "anthropic": "Anthropic Claude"}
OFFICIAL = {"openai": "https://platform.openai.com/", "anthropic": "https://console.anthropic.com/"}

# Response: (status_code, parsed_json_or_None, raw_text)
Transport = Callable[[str, str, dict, dict | None, float], tuple[int, Any, str]]


def requests_transport(method: str, url: str, headers: dict, body: dict | None, timeout: float) -> tuple[int, Any, str]:
    r = requests.request(method, url, headers=headers, json=body, timeout=timeout)
    try:
        js = r.json()
    except ValueError:
        js = None
    return r.status_code, js, r.text[:2000]


class ProviderError(RuntimeError):
    """code: missing_key | invalid_key | permission | insufficient_credit | rate_limited | model_not_found |
    bad_request | timeout | network | provider_unavailable | unknown"""

    def __init__(self, code: str, message: str, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.code, self.retryable, self.status = code, retryable, status


USER_MESSAGES = {
    "missing_key": "No API key is configured for this provider. Add your own key in AI Provider Setup.",
    "invalid_key": "The provider rejected the API key (invalid or revoked). Replace it in AI Provider Setup.",
    "permission": "The API key does not have permission for this request or model.",
    "insufficient_credit": "Your provider account has no available credit or billing is not set up. Fix this in the provider's billing dashboard.",
    "rate_limited": "The provider rate-limited the request. Retry later or lower your usage.",
    "model_not_found": "The selected model is not available to your API key. Choose another model.",
    "bad_request": "The provider rejected the request.",
    "timeout": "The provider did not respond in time.",
    "network": "Network error while contacting the provider.",
    "provider_unavailable": "The provider is temporarily unavailable (outage or overload).",
    "unknown": "Unexpected provider error.",
}


@dataclass
class Completion:
    provider: str
    model: str
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    request_id: str | None = None
    latency_s: float = 0.0
    raw_usage: dict = field(default_factory=dict)


def _classify(provider: str, status: int, js: Any, text: str, key: str) -> ProviderError:
    err = (js or {}).get("error", {}) if isinstance(js, dict) else {}
    etype = str(err.get("type") or "") if isinstance(err, dict) else ""
    ecode = str(err.get("code") or "") if isinstance(err, dict) else ""
    msg = redact(str(err.get("message") if isinstance(err, dict) else text)[:300], key)
    low = msg.lower()
    if status == 401:
        return ProviderError("invalid_key", msg, False, status)
    if status == 403:
        return ProviderError("permission", msg, False, status)
    if status == 404 or ecode == "model_not_found" or etype == "not_found_error":
        return ProviderError("model_not_found", msg, False, status)
    if ecode == "insufficient_quota" or "credit balance" in low or "billing" in low and status in (400, 402, 429):
        return ProviderError("insufficient_credit", msg, False, status)
    if status == 429:
        return ProviderError("rate_limited", msg, True, status)
    if status in (500, 502, 503, 504, 529):
        return ProviderError("provider_unavailable", msg, True, status)
    if status == 400:
        return ProviderError("bad_request", msg, False, status)
    return ProviderError("unknown", msg, False, status)


class ProviderClient:
    provider = ""

    def __init__(self, api_key: str, transport: Transport | None = None, timeout: float = 60):
        if not api_key:
            raise ProviderError("missing_key", USER_MESSAGES["missing_key"])
        self._key = api_key            # never logged, never returned
        self.transport = transport or requests_transport
        self.timeout = timeout

    def __repr__(self) -> str:        # make accidental logging of the object safe
        return f"<{type(self).__name__} key=[REDACTED]>"

    def _send(self, method: str, url: str, headers: dict, body: dict | None) -> tuple[Any, float]:
        t0 = time.perf_counter()
        try:
            status, js, text = self.transport(method, url, headers, body, self.timeout)
        except requests.Timeout as e:
            raise ProviderError("timeout", USER_MESSAGES["timeout"], True) from e
        except requests.RequestException as e:
            raise ProviderError("network", redact(f"{USER_MESSAGES['network']} ({type(e).__name__})", self._key), True) from e
        if status >= 400:
            raise _classify(self.provider, status, js, text, self._key)
        return js, round(time.perf_counter() - t0, 3)

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def complete(self, model: str, system: str, user: str, max_output_tokens: int) -> Completion:
        raise NotImplementedError


class OpenAIClient(ProviderClient):
    provider = "openai"

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    def list_models(self) -> list[str]:
        """Free endpoint (no token charges) - used for 'Test connection'."""
        js, _ = self._send("GET", f"{OPENAI_BASE}/models", self._h(), None)
        return sorted(m["id"] for m in (js or {}).get("data", []) if isinstance(m, dict) and "id" in m)

    def complete(self, model: str, system: str, user: str, max_output_tokens: int) -> Completion:
        body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_completion_tokens": int(max_output_tokens)}
        js, lat = self._send("POST", f"{OPENAI_BASE}/chat/completions", self._h(), body)
        u = (js or {}).get("usage") or {}
        text = "".join(((c.get("message") or {}).get("content") or "") for c in (js or {}).get("choices", []))
        return Completion("openai", js.get("model", model), text, u.get("prompt_tokens"), u.get("completion_tokens"),
                          (u.get("prompt_tokens_details") or {}).get("cached_tokens"), None, js.get("id"), lat, u)


class AnthropicClient(ProviderClient):
    provider = "anthropic"

    def _h(self) -> dict:
        return {"x-api-key": self._key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}

    def list_models(self) -> list[str]:
        js, _ = self._send("GET", f"{ANTHROPIC_BASE}/models?limit=100", self._h(), None)
        return sorted(m["id"] for m in (js or {}).get("data", []) if isinstance(m, dict) and "id" in m)

    def complete(self, model: str, system: str, user: str, max_output_tokens: int) -> Completion:
        body = {"model": model, "max_tokens": int(max_output_tokens), "system": system,
                "messages": [{"role": "user", "content": user}]}
        js, lat = self._send("POST", f"{ANTHROPIC_BASE}/messages", self._h(), body)
        u = (js or {}).get("usage") or {}
        text = "".join(b.get("text", "") for b in (js or {}).get("content", []) if isinstance(b, dict))
        return Completion("anthropic", js.get("model", model), text, u.get("input_tokens"), u.get("output_tokens"),
                          u.get("cache_read_input_tokens"), u.get("cache_creation_input_tokens"), js.get("id"), lat, u)


CLIENTS = {"openai": OpenAIClient, "anthropic": AnthropicClient}


def looks_like_key(provider: str, key: str) -> str | None:
    """Cheap format check before any network call. Returns an error message or None."""
    k = (key or "").strip()
    if not k:
        return "API key is empty"
    if any(ch.isspace() for ch in k) or len(k) < 20 or len(k) > 400:
        return "API key format looks wrong (length/whitespace)"
    if provider == "anthropic" and not k.startswith("sk-ant-"):
        return "Anthropic API keys start with 'sk-ant-'"
    if provider == "openai" and (not k.startswith("sk-") or k.startswith("sk-ant-")):
        return "OpenAI API keys start with 'sk-' (this looks like an Anthropic key)" if k.startswith("sk-ant-") else "OpenAI API keys start with 'sk-'"
    return None

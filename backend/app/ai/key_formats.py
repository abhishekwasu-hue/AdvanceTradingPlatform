"""Settings redesign part A (review R5): the ONE table of AI key formats - which provider a pasted key belongs to.

The connect panel ("Connect AI in one paste") reads the provider from the key itself; the server checks again here and is
the authority. The order of `PREFIXES` matters (first match wins):

1. Things people paste that are not API keys are refused first: sign-in sessions, OAuth access / refresh tokens and
   cookies (a subscription such as Claude Pro cannot be used by another app), JWTs, and organisation admin keys.
2. Master Order 2 part A: OpenRouter `sk-or-` and Groq `gsk_` are providers of their own (OpenAI-compatible APIs) -
   matched BEFORE the plain `sk-` row, so they are never read as an OpenAI key. Keys of services ATP does not take
   (xAI `xai-`) are refused with a plain "not supported" line.
3. `sk-ant-` is Anthropic; `sk-proj-`, `sk-svcacct-` and any other `sk-` is OpenAI; `AIza` is Google Gemini.
4. Mistral and Z.ai keys have no fixed prefix: they come out "unknown" and the panel's small picker asks which service
   the key is from (unknown formats always get the picker).
5. MO2A-2: Cloudflare Workers AI needs two things, an API token (`cfut_` user token / `cfat_` account token, the
   scannable formats; older tokens are 40 letters and numbers with no prefix) and the 32-character Account ID. Both may
   come in ONE paste (the token and the Account ID side by side, in either order: `split_account_id`), or the panel
   asks for the Account ID in a second box. The Global API Key (`cfk_`) opens the whole Cloudflare account: refused.

`PROVIDER_FACTS` is the one table of what the Connect AI panel and the key guides say about each provider: the plain
name, the "Free tier" and "Reads charts (vision)" tags, whether its free tier may train on what it is sent (R4 / SR-2:
ATP's private-method features refuse such a key until the user confirms a paid plan) and the one-line data term.

`backend/tests/fixtures/key_formats.json` holds the same table and the test cases; the frontend's
`src/aiKeys/keyFormats.ts` reads a byte-identical copy (`src/aiKeys/__fixtures__/key_formats.json`), so the two sides
cannot drift (tests/test_settings_a_key_formats.py and src/aiKeys/keyFormats.test.ts).

Nothing here keeps, logs or echoes a key.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Literal, Optional, Tuple

Kind = Literal["provider", "unsupported", "session", "admin", "unknown", "empty"]

# (prefix, kind, provider id or "", plain name). Compared case-sensitively, except the "session" rows (lower-cased key).
PREFIXES: Tuple[Tuple[str, str, str, str], ...] = (
    ("sk-ant-sid", "session", "", "Claude sign-in session"),
    ("sk-ant-oat", "session", "", "Claude sign-in token"),
    ("sk-ant-ort", "session", "", "Claude sign-in token"),
    ("ya29.", "session", "", "Google sign-in token"),
    ("1//", "session", "", "Google sign-in token"),
    ("sess-", "session", "", "OpenAI sign-in session"),
    ("eyj", "session", "", "sign-in token"),
    ("bearer ", "session", "", "sign-in token"),
    ("sk-ant-admin", "admin", "", "Anthropic admin key"),
    ("sk-admin-", "admin", "", "OpenAI admin key"),
    ("cfk_", "admin", "", "Cloudflare Global API Key"),
    ("sk-or-", "provider", "openrouter", "OpenRouter"),
    ("gsk_", "provider", "groq", "Groq"),
    ("cfut_", "provider", "cloudflare", "Cloudflare Workers AI"),
    ("cfat_", "provider", "cloudflare", "Cloudflare Workers AI"),
    ("xai-", "unsupported", "", "xAI"),
    ("sk-ant-", "provider", "anthropic", "Claude"),
    ("sk-proj-", "provider", "openai", "OpenAI"),
    ("sk-svcacct-", "provider", "openai", "OpenAI"),
    ("sk-", "provider", "openai", "OpenAI"),
    ("AIza", "provider", "gemini", "Google Gemini"),
)
# Words that only appear in a pasted cookie / token, never in an API key.
SESSION_MARKERS = ("session", "cookie", "oauth", "refresh_token", "access_token")

# Master Order 2 part A: what each provider is, in the words the panel and the guides use. Sources and the day they were
# checked are on each provider's request shape in app/ai/providers.py (OPENAI_COMPATIBLE) and in the key guides
# (frontend/src/aiKeys/guides.ts `checked_on`).
# - free_tier: a key works without paying (with low limits). vision: ATP sends chart images to it.
# - may_train: its free tier may use what it is sent to improve or train models (unknown counts as yes).
PROVIDER_FACTS: Dict[str, Dict[str, Any]] = {
    "gemini": {"name": "Google Gemini", "free_tier": True, "vision": True, "may_train": True,
               "privacy": "On the free tier Google may use what you send to improve its products. A key with billing turned on is not used that way."},
    "anthropic": {"name": "Claude", "free_tier": False, "vision": True, "may_train": False,
                  "privacy": "Anthropic says it does not train its models on what you send through an API key."},
    "openai": {"name": "OpenAI", "free_tier": False, "vision": True, "may_train": False,
               "privacy": "OpenAI says it does not train its models on what you send through an API key unless you opt in."},
    "openrouter": {"name": "OpenRouter", "free_tier": True, "vision": True, "may_train": True,
                   "privacy": "OpenRouter passes your request to another company's model. Some free models keep and use what you send."},
    "groq": {"name": "Groq", "free_tier": True, "vision": False, "may_train": False,
             "privacy": "Groq says it does not keep what you send by default."},
    "mistral": {"name": "Mistral", "free_tier": True, "vision": True, "may_train": True,
                "privacy": "On the free plan Mistral may use what you send to improve its models unless you opt out in its settings."},
    "zai": {"name": "Z.ai GLM", "free_tier": True, "vision": False, "may_train": True,
            "privacy": "Z.ai does not say clearly what its free models keep, so AiAlgoDesk treats them as if they may use what you send."},
    # MO2A-2 (developers.cloudflare.com/workers-ai/platform/data-usage, read 2026-10-22): no retention after the
    # answer and no training on what is sent; 10,000 free Neurons a day on every plan (platform/pricing).
    "cloudflare": {"name": "Cloudflare Workers AI", "free_tier": True, "vision": True, "may_train": False,
                   "privacy": "Cloudflare says it does not keep what you send after answering and does not train models on it."},
}
PROVIDER_NAMES = {provider: str(facts["name"]) for provider, facts in PROVIDER_FACTS.items()}
# Review R4 / SR-2: a provider whose free tier may use what is sent to train its models. ATP's private method prompts
# (chart analysis) never go to such a key until the user confirms the key is on a paid plan.
MAY_TRAIN_ON_FREE_TIER = {provider: bool(facts["may_train"]) for provider, facts in PROVIDER_FACTS.items()}
# The providers the Connect AI panel takes (behind `ai_one_key`), in the order the picker and the guides show them:
# free Google Gemini first.
CONNECT_ORDER = ("gemini", "anthropic", "openai", "openrouter", "groq", "mistral", "zai", "cloudflare")
# The ones whose keys have no fixed prefix (or a prefix-less format), offered by the picker for an unknown key. Cloudflare
# is here for its older, prefix-less tokens.
PICKER_ORDER = ("gemini", "anthropic", "openai", "mistral", "zai", "cloudflare")
# MO2A-2: a Cloudflare Account ID (32 hex characters, as the dashboard shows it), and what may sit between it and the token
# in one paste (spaces, a line break or a comma).
ACCOUNT_ID = re.compile(r"^[0-9a-fA-F]{32}$")
_PAIR_SEPARATOR = re.compile(r"[\s,]+")


@dataclass(frozen=True)
class Detection:
    kind: Kind
    provider: Optional[str] = None      # a PROVIDER_FACTS id when kind == "provider"
    name: str = ""                      # the plain name of what was pasted


def clean(raw: Optional[str]) -> str:
    """The key as pasted, without the spaces, line breaks and quotes a copy often brings along."""
    return (raw or "").strip().strip("\"'`").strip()


def looks_like_session(key: str) -> bool:
    """A cookie header, a JWT, an OAuth token or a sign-in session - never an API key."""
    lower = key.lower()
    if any(lower.startswith(prefix) for prefix, kind, _, _ in PREFIXES if kind == "session"):
        return True
    if any(marker in lower for marker in SESSION_MARKERS):
        return True
    return "=" in key or ";" in key or key.count(".") >= 2


def split_account_id(raw: Optional[str]) -> Tuple[Optional[str], str]:
    """MO2A-2: (account id, the rest) when the paste is exactly two parts and one of them is a Cloudflare Account ID;
    else (None, the paste). The id comes back lower-case."""
    key = clean(raw)
    parts = [part.strip("\"'`") for part in _PAIR_SEPARATOR.split(key) if part]
    if len(parts) != 2:
        return None, key
    ids = [part for part in parts if ACCOUNT_ID.match(part)]
    if len(ids) != 1:
        return None, key
    account = ids[0]
    return account.lower(), next(part for part in parts if part is not account)


def detect_provider(raw: Optional[str]) -> Detection:
    """Which provider a key belongs to, from its format alone (no call is made). A paste of a token next to a Cloudflare
    Account ID is read from the token; a prefix-less token next to an Account ID is a (older) Cloudflare token."""
    key = clean(raw)
    if not key:
        return Detection("empty")
    account, rest = split_account_id(key)
    if account is not None:
        found = detect_provider(rest)
        return Detection("provider", "cloudflare", PROVIDER_NAMES["cloudflare"]) if found.kind == "unknown" else found
    if looks_like_session(key):
        found = next((name for prefix, kind, _, name in PREFIXES if kind == "session" and key.lower().startswith(prefix)), "sign-in token")
        return Detection("session", None, found)
    for prefix, kind, provider, name in PREFIXES:
        if kind == "session":
            continue
        if key.startswith(prefix) or (kind == "admin" and key.lower().startswith(prefix)):
            return Detection(kind, provider or None, name)  # type: ignore[arg-type]
    return Detection("unknown")


def table() -> list:
    """The table as plain rows (the shared fixture holds the same list)."""
    return [{"prefix": p, "kind": k, "provider": prov or None, "name": n} for p, k, prov, n in PREFIXES]


def facts() -> Dict[str, Dict[str, Any]]:
    """The provider facts as plain rows (the shared fixture holds the same table)."""
    return {provider: dict(row) for provider, row in PROVIDER_FACTS.items()}

"""Settings redesign part A (review R5): the ONE table of AI key formats - which provider a pasted key belongs to.

The connect panel ("Connect AI in one paste") reads the provider from the key itself; the server checks again here and is
the authority. The order of `PREFIXES` matters (first match wins):

1. Things people paste that are not API keys are refused first: sign-in sessions, OAuth access / refresh tokens and
   cookies (a subscription such as Claude Pro cannot be used by another app), JWTs, and organisation admin keys.
2. Keys of services ATP does not take yet (OpenRouter `sk-or-`, Groq `gsk_`, xAI `xai-`) are refused with a plain
   "not supported yet" line - never read as an OpenAI key because they happen to start with `sk-`.
3. `sk-ant-` is Anthropic; `sk-proj-`, `sk-svcacct-` and any other `sk-` is OpenAI; `AIza` is Google Gemini.

`backend/tests/fixtures/key_formats.json` holds the same table and the test cases; the frontend's
`src/aiKeys/keyFormats.ts` reads a byte-identical copy (`src/aiKeys/__fixtures__/key_formats.json`), so the two sides
cannot drift (tests/test_settings_a_key_formats.py and src/aiKeys/keyFormats.test.ts).

Nothing here keeps, logs or echoes a key.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Tuple

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
    ("sk-or-", "unsupported", "", "OpenRouter"),
    ("gsk_", "unsupported", "", "Groq"),
    ("xai-", "unsupported", "", "xAI"),
    ("sk-ant-", "provider", "anthropic", "Claude"),
    ("sk-proj-", "provider", "openai", "OpenAI"),
    ("sk-svcacct-", "provider", "openai", "OpenAI"),
    ("sk-", "provider", "openai", "OpenAI"),
    ("AIza", "provider", "gemini", "Google Gemini"),
)
# Words that only appear in a pasted cookie / token, never in an API key.
SESSION_MARKERS = ("session", "cookie", "oauth", "refresh_token", "access_token")

PROVIDER_NAMES = {"anthropic": "Claude", "openai": "OpenAI", "gemini": "Google Gemini"}
# Review R4 / SR-2: a provider whose free tier may use what is sent to train its models. ATP's private method prompts
# (chart analysis) never go to such a key until the user confirms the key is on a paid plan.
MAY_TRAIN_ON_FREE_TIER = {"anthropic": False, "openai": False, "gemini": True}


@dataclass(frozen=True)
class Detection:
    kind: Kind
    provider: Optional[str] = None      # anthropic / openai / gemini when kind == "provider"
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


def detect_provider(raw: Optional[str]) -> Detection:
    """Which provider a key belongs to, from its format alone (no call is made)."""
    key = clean(raw)
    if not key:
        return Detection("empty")
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

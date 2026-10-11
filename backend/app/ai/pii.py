"""H-C1 e: personal data is masked in the stored copy of every LLM call (`llm_calls`).

What is masked, and where:
* everywhere (system prompt, the trader's text, the answer): e-mail addresses, PAN numbers, Aadhaar numbers written in
  4-4-4 groups, and an account / client id written after a label ("a/c 1234567890", "client id AB1234");
* in the trader's own text and in the answer: Indian mobile numbers and 12-digit runs (an Aadhaar written without
  spaces). The system prompt is left alone for these two because it carries market figures (volumes, turnover)
  that can look like them.

Only the stored copy is masked: the model saw the original, and the row's SHA-256 hashes are of the original text, so
a trader's own copy can still be matched against the audit record.
"""
import re
from typing import Optional

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Za-z0-9])")
_AADHAAR_SPACED = re.compile(r"(?<!\d)\d{4}[ -]\d{4}[ -]\d{4}(?!\d)")
_ACCOUNT = re.compile(r"(?i)\b(?:a/c|acct|account|client\s*id|client\s*code|demat|bo\s*id|ucc)\b(?:\s*(?:no\.?|number|#))?\s*[:\-]?\s*[A-Za-z0-9]{4,20}")
_MOBILE = re.compile(r"(?<![\d.])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\d.])")
_DIGITS12 = re.compile(r"(?<![\d.])\d{12}(?![\d.])")


def _label(match: re.Match) -> str:
    text = match.group(0)
    head = re.match(r"(?i)\s*(?:a/c|acct|account|client\s*id|client\s*code|demat|bo\s*id|ucc)(?:\s*(?:no\.?|number|#))?\s*[:\-]?\s*", text)
    return (head.group(0) if head else "") + "[account]"


def redact(text: Optional[str], *, strict: bool = True) -> Optional[str]:
    """The text with personal data masked; `strict` adds mobile numbers and bare 12-digit runs (not for system prompts)."""
    if text is None:
        return None
    out = _EMAIL.sub("[email]", text)
    out = _PAN.sub("[pan]", out)
    out = _AADHAAR_SPACED.sub("[aadhaar]", out)
    out = _ACCOUNT.sub(_label, out)
    if strict:
        out = _MOBILE.sub("[phone]", out)
        out = _DIGITS12.sub("[id]", out)
    return out


__all__ = ["redact"]

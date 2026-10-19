"""Credential redaction for logs, exceptions and API responses."""
from __future__ import annotations

import logging
import re

# OpenAI keys start with "sk-" (incl. sk-proj-, sk-svcacct-); Anthropic keys with "sk-ant-".
_PATTERNS = [re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
             re.compile(r"(?i)(x-api-key|authorization)(\"?\s*[:=]\s*\"?)(bearer\s+)?[A-Za-z0-9_\-\.]{8,}")]


def redact(text: str, *secrets: str | None) -> str:
    s = str(text)
    for sec in secrets:
        if sec:
            s = s.replace(sec, "[REDACTED]")
    s = _PATTERNS[0].sub("[REDACTED]", s)
    s = _PATTERNS[1].sub("[REDACTED]", s)
    s = _PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", s)
    return s


def mask(key: str) -> str:
    """Display form: provider prefix + last 4 characters."""
    if not key:
        return ""
    prefix = "sk-ant-" if key.startswith("sk-ant-") else "sk-" if key.startswith("sk-") else ""
    return f"{prefix}…{key[-4:]}"


class RedactingFilter(logging.Filter):
    """Attach to the root logger: scrubs credential patterns from every record (message, args, exception text)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        record.msg, record.args = redact(msg), ()
        if record.exc_info:
            import traceback
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


def install_log_redaction() -> None:
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for f in root.filters):
        root.addFilter(RedactingFilter())
    for h in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in h.filters):
            h.addFilter(RedactingFilter())

"""Production readiness A2: the error tracker - the Sentry SDK pointed at a DSN from the environment, OFF when unset.

`ERROR_TRACKER_DSN` (a GlitchTip or Sentry project's DSN, from the owner's account; never in git) turns it on for the
API and the trading worker. Empty = nothing is initialised and nothing leaves the server. A self-hosted Sentry or
GlitchTip does not fit next to production on the 8 GB host, so the default plan is a hosted free tier (GlitchTip or
Sentry), listed as a processor (A5); docs/deploy/HOSTINGER_RUNBOOK.md has the owner's steps.

Every event and breadcrumb passes `scrub_event` / `scrub_breadcrumb` before it is sent:

* dropped whole: cookies, request bodies, query strings, environment, the user block except a numeric id, every
  stack frame's local variables, and any header that carries a credential (`Authorization`, `Cookie`, `X-Api-Key`, ...);
* masked inside every remaining string: e-mail addresses, phone numbers (Indian and international), JWTs, bearer and
  basic credentials, `key=value` pairs whose key names a secret, Telegram bot tokens, long hex / base64 blobs (keys,
  hashes), and URL credentials (`https://user:pass@host`);
* any dictionary key that names a secret (`password`, `token`, `secret`, `api_key`, `dsn`, ...) has its value replaced.

The SDK is also told not to collect PII itself (`send_default_pii=False`, no local variables, no request bodies, no
performance tracing). Only server errors are sent: logged ERRORs and unhandled exceptions.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from app.core import config

logger = logging.getLogger(__name__)

MASK = "[filtered]"
_state = {"on": False}

_SECRET_KEY = re.compile(r"(pass(word|wd|phrase)?|secret|token|api[_-]?key|apikey|auth(orization)?|cookie|session|csrf|"
                         r"credential|private[_-]?key|signature|dsn|otp|mfa|totp|pin|access[_-]?key|refresh)", re.I)
_DROP_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "x-telegram-bot-api-secret-token", "proxy-authorization",
                 "x-csrf-token", "x-forwarded-for", "x-real-ip", "forwarded"}
_PATTERNS = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[email]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"), "[jwt]"),
    (re.compile(r"\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}", re.I), r"\1 " + MASK),
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), "[bot-token]"),
    (re.compile(r"(?i)\b([a-z0-9_-]*(?:pass(?:word)?|secret|token|api[_-]?key|apikey|key|code|otp|pin|sig(?:nature)?)[a-z0-9_-]*)"
                r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s&,;]+)"), r"\1\2" + MASK),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1" + MASK + "@"),
    (re.compile(r"(?<![\w.])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\w.])"), "[phone]"),
    (re.compile(r"(?<![\w.])\+\d{1,3}[\s-]?\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}(?![\w.])"), "[phone]"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "[hex]"),
    (re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}"), "[blob]"),
]


def scrub_text(value: str) -> str:
    out = value
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return MASK
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {k: (MASK if isinstance(k, str) and _SECRET_KEY.search(k) else _scrub(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v, depth + 1) for v in value]
    return value


def _request(request: Any) -> Dict[str, Any]:
    if not isinstance(request, dict):
        return {}
    url = str(request.get("url") or "").split("?", 1)[0].split("#", 1)[0]
    raw = request.get("headers")
    headers: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    kept = {k: v for k, v in headers.items() if isinstance(k, str) and k.lower() not in _DROP_HEADERS and not _SECRET_KEY.search(k)}
    return _scrub({"method": request.get("method"), "url": url, "headers": kept})


def _frames(exception: Any) -> None:
    for item in (exception or {}).get("values") or []:
        for frame in ((item or {}).get("stacktrace") or {}).get("frames") or []:
            if isinstance(frame, dict):
                frame.pop("vars", None)


def scrub_event(event: Dict[str, Any], hint: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """`before_send`: the event with nothing personal or secret left in it."""
    if not isinstance(event, dict):
        return None
    event = dict(event)
    if "request" in event:
        event["request"] = _request(event["request"])
    raw_user = event.get("user")
    user: Dict[str, Any] = raw_user if isinstance(raw_user, dict) else {}
    uid = user.get("id")
    event["user"] = {"id": str(uid)} if isinstance(uid, int) or (isinstance(uid, str) and uid.isdigit()) else {}
    for key in ("server_name", "modules"):
        event.pop(key, None)
    contexts = event.get("contexts") if isinstance(event.get("contexts"), dict) else None
    if contexts is not None:
        contexts.pop("environment", None)
    _frames(event.get("exception"))
    _frames({"values": (event.get("threads") or {}).get("values") if isinstance(event.get("threads"), dict) else []})
    if isinstance(event.get("breadcrumbs"), dict):
        crumbs = [scrub_breadcrumb(c) for c in event["breadcrumbs"].get("values") or []]
        event["breadcrumbs"] = {"values": [c for c in crumbs if c]}
    return _scrub(event)


def scrub_breadcrumb(crumb: Dict[str, Any], hint: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """`before_breadcrumb`: HTTP breadcrumbs keep the method, the path and the status only."""
    if not isinstance(crumb, dict):
        return None
    crumb = dict(crumb)
    data = crumb.get("data") if isinstance(crumb.get("data"), dict) else None
    if data is not None:
        crumb["data"] = {k: v for k, v in data.items() if k in ("method", "status_code", "reason", "url")}
        if "url" in crumb["data"]:
            crumb["data"]["url"] = str(crumb["data"]["url"]).split("?", 1)[0]
    return _scrub(crumb)


def enabled() -> bool:
    return bool(_state["on"])


def init(component: str) -> bool:
    """Turns the tracker on for this process when ERROR_TRACKER_DSN is set. Never raises; False = off."""
    dsn = config.ERROR_TRACKER_DSN
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning("ERROR_TRACKER_DSN is set but the sentry-sdk package is missing; the error tracker stays off")
        return False
    try:
        sentry_sdk.init(
            dsn=dsn, environment=config.ERROR_TRACKER_ENVIRONMENT or config.ENVIRONMENT, release=config.APP_VERSION,
            sample_rate=config.ERROR_TRACKER_SAMPLE_RATE, traces_sample_rate=0.0, send_default_pii=False,
            include_local_variables=False, max_request_body_size="never", before_send=scrub_event,
            before_breadcrumb=scrub_breadcrumb, server_name=component, default_integrations=True,
            integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
        )
        sentry_sdk.set_tag("component", component)
    except Exception:  # noqa: BLE001 - a bad DSN never stops the API or the worker
        logger.warning("The error tracker could not start (check ERROR_TRACKER_DSN); it stays off")
        return False
    _state["on"] = True
    logger.info("Error tracker on for %s (events are scrubbed of personal data before sending)", component)
    return True


__all__ = ["scrub_event", "scrub_breadcrumb", "scrub_text", "init", "enabled"]

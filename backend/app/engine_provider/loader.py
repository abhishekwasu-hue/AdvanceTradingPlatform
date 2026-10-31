"""M66 EN-1: load the signal engine from the private package, the same way as Analyze a chart's engine
(app/chart_analyze/provider.py).

`load()` imports `atp_private.trade_engine` once per process and calls its `get_provider()`. On the host the package
is a read-only checkout on the import path (`PYTHONPATH=/opt/atp-private`, tag `ATP_PRIVATE_REF`; no pip install;
docs/design/PRIVATE_PACKAGE.md section 6). When it is absent (public CI, a developer box, any host without it) or
refused, `load()` returns None: the feature is unavailable, `reason()` says why in plain words (a module or an
exception class name at most) and nothing else in ATP is affected. The loader logs the engine's name, version and
interface number only - never any of its content.

The only module of this package that names `atp_private` (a public test checks it). Public tests use a synthetic
test double (`set_for_tests`, or a fake module in `sys.modules` inside the test).
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Optional

from app.engine_provider.contract import INTERFACE_VERSION, TradeEngineProvider

logger = logging.getLogger(__name__)

MODULE = "atp_private.trade_engine"
NOT_INSTALLED = "Signal engine not installed"
REASON_ABSENT = "the private package is not installed on this server"
OWNER_STEPS = ("The owner adds the host's read-only deploy key (~/.ssh/atp_private_ro) and ATP_PRIVATE_REF (a tag of "
               "atp-private that holds the signal engine) to /opt/atp/.env, then runs the deploy; the same key and tag "
               "serve Analyze a chart's engine (docs/deploy/HOSTINGER_RUNBOOK.md, 'Chart analysis engine').")
TEXT_MAX = 80          # name / version / tag are cut to this and stripped of unprintable characters

_UNSET = object()
_cached: Any = _UNSET
_override: Any = _UNSET
_reason: str = REASON_ABSENT
_module_tag: str = ""


def _clean(value: object) -> str:
    return "".join(ch for ch in str(value) if ch.isprintable())[:TEXT_MAX]


def _refused(reason: str) -> Optional[TradeEngineProvider]:
    global _reason
    _reason = reason
    return None


def _import() -> Optional[TradeEngineProvider]:
    global _module_tag
    try:
        module = importlib.import_module(MODULE)
    except ImportError as exc:
        missing = getattr(exc, "name", None) or ""
        if isinstance(exc, ModuleNotFoundError) and (missing == "atp_private" or MODULE.startswith(missing + ".") or missing == MODULE):
            return _refused(REASON_ABSENT)
        logger.warning("%s could not be imported (%s %s); the signal engine stays off", MODULE, exc.__class__.__name__, missing or "-")
        return _refused(f"the private package could not be imported ({exc.__class__.__name__}{': ' + missing if missing else ''})")
    except Exception as exc:  # a broken private package must not break ATP
        logger.warning("%s failed while importing (%s); the signal engine stays off", MODULE, exc.__class__.__name__)
        return _refused(f"the private package failed while importing ({exc.__class__.__name__})")
    factory = getattr(module, "get_provider", None)
    if not callable(factory):
        logger.warning("%s has no get_provider(); the signal engine stays off", MODULE)
        return _refused("the private package has no get_provider()")
    try:
        provider = factory()
    except Exception as exc:  # a broken private package must not break ATP
        logger.warning("%s failed to start (%s); the signal engine stays off", MODULE, exc.__class__.__name__)
        return _refused(f"the engine failed to start ({exc.__class__.__name__})")
    if not isinstance(provider, TradeEngineProvider):
        logger.warning("%s returned an object without the engine interface; the signal engine stays off", MODULE)
        return _refused("the engine does not match ATP's engine interface")
    interface = getattr(provider, "interface", None)
    if interface != INTERFACE_VERSION:
        logger.warning("%s implements interface %s, ATP supports %s; the signal engine stays off", MODULE, _clean(interface), INTERFACE_VERSION)
        return _refused(f"the engine implements interface {_clean(interface)}, this release supports {INTERFACE_VERSION}")
    _module_tag = _clean(getattr(module, "TAG", "") or getattr(module, "__version__", "") or "")
    logger.info("Signal engine loaded: %s %s (interface %s)", _clean(provider.name), _clean(provider.version), INTERFACE_VERSION)
    return provider


def load() -> Optional[TradeEngineProvider]:
    """The installed engine, or None. Imported once per process."""
    global _cached
    if _override is not _UNSET:
        return _override
    if _cached is _UNSET:
        _cached = _import()
    return _cached


def available() -> bool:
    return load() is not None


def reason() -> str:
    """Why the engine is off (meaningful only when load() is None)."""
    return REASON_ABSENT if _override is not _UNSET else _reason


def tag(engine: Optional[TradeEngineProvider] = None) -> str:
    """The private package's release tag: the provider's `tag`, else the module's `TAG` / `__version__`, else ""."""
    engine = engine if engine is not None else load()
    if engine is None:
        return ""
    return _clean(getattr(engine, "tag", "") or "") or ("" if _override is not _UNSET else _module_tag)


def describe(*, admin: bool = False) -> dict[str, Any]:
    """The engine's state, for the admin System view and `deploy/atp.sh health` only (never a user surface).
    `admin` adds why it is missing and what the owner does. `checkout` is the host's checkout of the private
    package (ATP_PRIVATE_VERSION, set by the deploy), "" when unknown."""
    engine = load()
    checkout = _clean(os.environ.get("ATP_PRIVATE_VERSION", "") or "")
    checkout = "" if checkout == "none" else checkout
    if engine is None:
        out: dict[str, Any] = {"installed": False, "message": NOT_INSTALLED, "interface_supported": INTERFACE_VERSION}
        if admin:
            out.update(reason=reason(), owner_steps=OWNER_STEPS, checkout=checkout)
        return out
    return {"installed": True, "name": _clean(engine.name), "version": _clean(engine.version), "tag": tag(engine),
            "interface": INTERFACE_VERSION, "checkout": checkout}


def set_for_tests(provider: Optional[TradeEngineProvider]) -> None:
    """Tests: use this provider (None = "not installed") instead of importing the private package."""
    global _override
    _override = provider


def reset_for_tests() -> None:
    global _override, _cached, _reason, _module_tag
    _override = _UNSET
    _cached = _UNSET
    _reason = REASON_ABSENT
    _module_tag = ""

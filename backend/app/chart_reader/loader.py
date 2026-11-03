"""M86 W1: load the chart reader from the private package, the same way as Analyze a chart's engine
(app/chart_analyze/provider.py) and the signal engine (app/engine_provider/loader.py).

`load()` imports the private module once per process and calls its `get_reader()`. When the package is absent (public
CI, a developer box, any host without it) or refused, `load()` returns None and `reason()` says why in plain words (a
module or an exception class name at most). The loader logs the reader's name and version only - never any of its text.

The only module of app/chart_reader that names the private package (tests/test_m86_chart_reader.py checks it). Public
tests use a synthetic fake reader (`set_for_tests`).
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

MODULE = "atp_private.chart_reader"
NOT_INSTALLED = "The chart reader is not installed on this server yet."
REASON_ABSENT = "the private package is not installed on this server"
TEXT_MAX = 80


@runtime_checkable
class Reader(Protocol):
    """What the private package implements (docs/design/M86_PLAN.md, "Contract"). `strategies()` gives labels only;
    `read()` returns the reader's own result dict, which ATP passes through unchanged."""

    name: str
    version: str
    ABHI: str

    def strategies(self) -> list[dict[str, Any]]: ...

    def two_chart_conditions(self, strategy: str) -> list[str]: ...

    def read(self, *, instrument: str, charts: list[tuple[str, bytes, str]], facts: str, strategy: str, model: str,
             effort: str, client: Any) -> dict[str, Any]: ...


_UNSET = object()
_cached: Any = _UNSET
_override: Any = _UNSET
_reason: str = REASON_ABSENT


def _clean(value: object) -> str:
    return "".join(ch for ch in str(value) if ch.isprintable())[:TEXT_MAX]


def _refused(reason: str) -> Optional[Reader]:
    global _reason
    _reason = reason
    return None


def _import() -> Optional[Reader]:
    try:
        module = importlib.import_module(MODULE)
    except ImportError as exc:
        missing = getattr(exc, "name", None) or ""
        if isinstance(exc, ModuleNotFoundError) and (missing == "atp_private" or MODULE.startswith(missing + ".") or missing == MODULE):
            return _refused(REASON_ABSENT)
        logger.warning("%s could not be imported (%s %s); the chart reader stays off", MODULE, exc.__class__.__name__, missing or "-")
        return _refused(f"the private package could not be imported ({exc.__class__.__name__}{': ' + missing if missing else ''})")
    except Exception as exc:  # noqa: BLE001 - a broken private package must not break ATP
        logger.warning("%s failed while importing (%s); the chart reader stays off", MODULE, exc.__class__.__name__)
        return _refused(f"the private package failed while importing ({exc.__class__.__name__})")
    factory = getattr(module, "get_reader", None)
    if not callable(factory):
        logger.warning("%s has no get_reader(); the chart reader stays off", MODULE)
        return _refused("the private package has no get_reader()")
    try:
        reader = factory()
    except Exception as exc:  # noqa: BLE001 - a broken private package must not break ATP
        logger.warning("%s failed to start (%s); the chart reader stays off", MODULE, exc.__class__.__name__)
        return _refused(f"the reader failed to start ({exc.__class__.__name__})")
    if not isinstance(reader, Reader):
        logger.warning("%s returned an object without the reader interface; the chart reader stays off", MODULE)
        return _refused("the reader does not match ATP's reader interface")
    logger.info("Chart reader loaded: %s %s", _clean(reader.name), _clean(reader.version))
    return reader


def load() -> Optional[Reader]:
    """The installed reader, or None. Imported once per process."""
    global _cached
    if _override is not _UNSET:
        return _override
    if _cached is _UNSET:
        _cached = _import()
    return _cached


def available() -> bool:
    return load() is not None


def reason() -> str:
    """Why the reader is off (meaningful only when load() is None)."""
    return REASON_ABSENT if _override is not _UNSET else _reason


def version() -> str:
    reader = load()
    return _clean(reader.version) if reader is not None else ""


def set_for_tests(reader: Optional[Reader]) -> None:
    """Tests: use this reader (None = "not installed") instead of importing the private package."""
    global _override
    _override = reader


def reset_for_tests() -> None:
    global _override, _cached, _reason
    _override = _UNSET
    _cached = _UNSET
    _reason = REASON_ABSENT

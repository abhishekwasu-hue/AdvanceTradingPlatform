"""Analyze a chart: the boundary to the analysis engine in the private package (docs/design/PRIVATE_PACKAGE.md §4).

The method - the instructions the model reads and how its answer is read - is NOT in this public repository. It lives
in the private package (`atp_private`), installed on the host only. ATP's public code holds just this:

- `AnalysisProvider`, the interface the private package implements: for an image-only analysis it returns the
  instructions (system text, user text, a prompt version) and parses the model's reply into the generic output
  contract (`app.chart_analyze.contract`). PR-C adds the hybrid entry point with the same output contract.
- `load()`, which imports `atp_private.chart_analyze` and calls its `get_provider()`. When the package is not
  installed (public CI, a developer box, any host without it), `load()` returns None and the feature answers "Analysis
  engine not installed" - nothing crashes and the rest of ATP is unaffected. The loader logs the engine's name and
  version only, never any of its text.

Public tests use a synthetic fake provider (`set_for_tests`). Nothing here holds or prints a word of the method.
"""
import importlib
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

MODULE = "atp_private.chart_analyze"
NOT_INSTALLED = "Analysis engine not installed"
# A1 (UI-H bug lane): why the engine is off, in plain words, for the admin and `deploy/atp.sh health` only. Each reason
# names a module or an exception class at most - never any of the engine's text.
REASON_ABSENT = "the private package is not installed on this server"
OWNER_STEPS = ("The owner adds the host's read-only deploy key (~/.ssh/atp_private_ro) and ATP_PRIVATE_REF (a tag of "
               "atp-private) to /opt/atp/.env, then runs the deploy (docs/deploy/HOSTINGER_RUNBOOK.md, 'Chart analysis engine').")


@dataclass(frozen=True)
class Instructions:
    system: str
    user: str
    prompt_version: str


@runtime_checkable
class AnalysisProvider(Protocol):
    """What the private package implements. `version` is the engine version, `method_version` the method pack's
    content hash; both are stored with every analysis (never the method itself)."""

    name: str
    version: str
    method_version: str

    def image_only_instructions(self, *, width: int, height: int) -> Instructions: ...

    def parse_image_only(self, reply: str, *, width: int, height: int) -> Dict[str, Any]: ...


_UNSET = object()
_cached: Any = _UNSET
_override: Any = _UNSET
_reason: str = REASON_ABSENT


def _refused(reason: str) -> Optional[AnalysisProvider]:
    global _reason
    _reason = reason
    return None


def _import() -> Optional[AnalysisProvider]:
    try:
        module = importlib.import_module(MODULE)
    except ImportError as exc:
        missing = getattr(exc, "name", None) or ""
        if isinstance(exc, ModuleNotFoundError) and (missing == "atp_private" or MODULE.startswith(missing + ".") or missing == MODULE):
            return _refused(REASON_ABSENT)
        logger.warning("%s could not be imported (%s %s); the analysis engine stays off", MODULE, exc.__class__.__name__, missing or "-")
        return _refused(f"the private package could not be imported ({exc.__class__.__name__}{': ' + missing if missing else ''})")
    factory = getattr(module, "get_provider", None)
    if not callable(factory):
        logger.warning("%s has no get_provider(); the analysis engine stays off", MODULE)
        return _refused("the private package has no get_provider()")
    try:
        provider = factory()
    except Exception as exc:  # noqa: BLE001 - a broken private package must not break ATP
        logger.warning("%s failed to start (%s); the analysis engine stays off", MODULE, exc.__class__.__name__)
        return _refused(f"the engine failed to start ({exc.__class__.__name__})")
    if not isinstance(provider, AnalysisProvider):
        logger.warning("%s returned an object without the AnalysisProvider interface; the engine stays off", MODULE)
        return _refused("the engine does not match ATP's analysis interface")
    logger.info("Chart analysis engine loaded: %s %s (method %s)", provider.name, provider.version, provider.method_version)
    return provider


def load() -> Optional[AnalysisProvider]:
    """The installed engine, or None. Imported once per process."""
    global _cached
    if _override is not _UNSET:
        return _override
    if _cached is _UNSET:
        _cached = _import()
    return _cached


def reason() -> str:
    """Why the engine is off (meaningful only when load() is None)."""
    return REASON_ABSENT if _override is not _UNSET else _reason


def describe(*, admin: bool = False) -> Dict[str, Any]:
    """The engine's state. `admin` (SUPER_ADMIN, `atp.sh health`) adds why it is missing and what the owner does."""
    engine = load()
    if engine is None:
        out: Dict[str, Any] = {"installed": False, "message": NOT_INSTALLED}
        if admin:
            out.update(reason=reason(), owner_steps=OWNER_STEPS)
        return out
    return {"installed": True, "name": engine.name, "version": engine.version, "method_version": engine.method_version}


def set_for_tests(provider: Optional[AnalysisProvider]) -> None:
    """Tests: use this provider (None = "not installed") instead of importing the private package."""
    global _override
    _override = provider


def reset_for_tests() -> None:
    global _override, _cached, _reason
    _override = _UNSET
    _cached = _UNSET
    _reason = REASON_ABSENT

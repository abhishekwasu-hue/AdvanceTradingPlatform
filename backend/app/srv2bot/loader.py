"""Load the 15M SRv2 bot from the private package (`atp_private.srv2`), the same way as the signal engine
(app/engine_provider/loader.py). Absent or broken -> None with a plain reason; nothing else in ATP is affected. The only module of
this package that names the private package."""
from __future__ import annotations

import importlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

MODULE = "atp_private.srv2"
REASON_ABSENT = "the private package is not installed on this server"

_UNSET = object()
_cached: Any = _UNSET
_reason = REASON_ABSENT


def load() -> Any | None:
    """The private bot's namespace: `host`, `runner`, and `modes` / `settings` / `positions` / `test_card` (None in an older package),
    or None."""
    global _cached, _reason
    if _cached is not _UNSET:
        return _cached
    try:
        host = importlib.import_module(MODULE + ".host")
        runner = importlib.import_module(MODULE + ".runner")
    except ImportError as exc:
        missing = getattr(exc, "name", None) or ""
        _reason = REASON_ABSENT if missing.startswith("atp_private") else f"could not be imported ({exc.__class__.__name__})"
        _cached = None
        return None
    except Exception as exc:  # noqa: BLE001 - a broken private package must not break ATP
        logger.warning("%s could not be loaded (%s); the bot stays off", MODULE, exc.__class__.__name__)
        _reason, _cached = f"could not be loaded ({exc.__class__.__name__})", None
        return None

    optional: dict[str, Any] = {}
    # SRV2 P3 (ADR-0029): the agent modes; P2 / P4: the settings (field list, validation) and the read-only positions.
    # An older private package has some of them only: the missing ones are None and their pages say so.
    # Owner 25 Nov: `test_card` - Trade's 🧪 TEST card (app/srv2bot/test_card.py runs it).
    for name in ("modes", "settings", "positions", "test_card"):
        try:
            optional[name] = importlib.import_module(f"{MODULE}.{name}")
        except ImportError:
            optional[name] = None
    from types import SimpleNamespace
    _cached = SimpleNamespace(host=host, runner=runner, **optional)
    return _cached


def reason() -> str:
    return _reason


def set_for_tests(value: Any) -> None:
    """Tests: a fake bot (or None); `_UNSET` restores loading."""
    global _cached
    _cached = value

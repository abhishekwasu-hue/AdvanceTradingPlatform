"""Runner S2 (docs/design/USER_RUNNER.md): which process this is - ATP's server, or the user's own runner.

The runner process (the runner package's entry point) turns runner mode on before it imports the pipeline (`enable_runner_mode`, or
ATP_RUNNER_MODE=1). The server never turns it on, so every function below reads exactly as before on the server.

Two seams read it (S1 carry-overs):
- the billing usage meter (`execution/order_persistence.py`): a server feature - a no-op in runner mode;
- the F&O ban list (`compliance/fo_limits.py`): the server reads its data lake; the runner reads the list ATP sends it
  (the link, step S4). Until a list has arrived it is UNKNOWN (None): a PAPER entry goes ahead with a note, a LIVE F&O
  entry is refused with a plain reason. Exits never read it (ADR-0004).
"""
from __future__ import annotations

import os
from collections.abc import Iterable

_runner: bool = os.environ.get("ATP_RUNNER_MODE", "").strip() == "1"
_ban_list: frozenset[str] | None = None


def is_runner() -> bool:
    """True only inside the user's runner process."""
    return _runner


def enable_runner_mode() -> None:
    """Called once by the runner's entry point, before anything places an order."""
    global _runner
    _runner = True
    os.environ["ATP_RUNNER_MODE"] = "1"


def set_ban_list(underlyings: Iterable[str] | None) -> None:
    """The exchange's F&O ban list as ATP sent it (S4); None = not received (unknown)."""
    global _ban_list
    _ban_list = None if underlyings is None else frozenset(u.strip().upper() for u in underlyings if u and u.strip())


def ban_list() -> frozenset[str] | None:
    """The last ban list received, or None when none has arrived yet (unknown)."""
    return _ban_list


__all__ = ["is_runner", "enable_runner_mode", "set_ban_list", "ban_list"]

#!/usr/bin/env python
"""How long one bcrypt cost-12 check takes on this machine: the median of 5 uncontended `bcrypt.checkpw` calls, in ms.

The login p95 of a load test follows this more than the code (12 CI runs: about 220 ms per check passed LD-9 at 500
logins over a 150 s ramp; about 450-570 ms failed it). In the report always; in the verdict only through
loadtest.login_budget() (CI's sign-in + probe runs: the login p95 budget is scaled up on a core slower than
REFERENCE_MS, never down, and the cost stays 12). Run with the app's own
bcrypt (the app hashes with `bcrypt.gensalt()`, cost 12), inside its container, read from stdin:

    docker compose ... exec -T backend python - < backend/scripts/bcrypt_baseline.py

(deploy/atp.sh loadtest, deploy/ci/stack.sh load). The password is a random throwaway made here: nothing is stored,
and only the time is printed. Bounded to 30 s.
"""
from __future__ import annotations

import secrets
import signal
import statistics
import time
from typing import Any, Optional

ROUNDS = 12
CHECKS = 5
TIMEOUT_S = 30
REFERENCE = "CI reference: ~220 ms passes LD-9 at 500 logins over a 150 s ramp, ~500 ms fails"
REFERENCE_MS = 220               # one uncontended cost-12 check on the CI core where LD-9's 2 s login p95 held


def measure(checks: int = CHECKS, rounds: int = ROUNDS) -> int:
    import bcrypt
    password = secrets.token_bytes(18)                     # a throwaway: never stored, never printed
    hashed = bcrypt.hashpw(password, bcrypt.gensalt(rounds))
    times = []
    for _ in range(checks):
        start = time.perf_counter()
        bcrypt.checkpw(password, hashed)
        times.append(time.perf_counter() - start)
    return round(statistics.median(times) * 1000)


def as_ms(value: Any) -> Optional[int]:
    """A measured time in whole ms, or None ("unknown") for anything else."""
    text = str(value if value is not None else "").strip()
    return int(text) if text.isascii() and text.isdigit() else None       # ASCII only: "²".isdigit() but int("²") fails


def note(cpu_model: Any, vcpus: Any, ms: Any) -> str:
    """'<model>, <n> vCPU; bcrypt-12 check: <ms> ms (CI reference: ...)', "unknown" for what could not be measured."""
    value = as_ms(ms)
    return (f"{str(cpu_model or '').strip() or 'unknown'}, {vcpus or '?'} vCPU; "
            f"bcrypt-12 check: {f'{value} ms' if value is not None else 'unknown'} ({REFERENCE})")


if __name__ == "__main__":
    if hasattr(signal, "alarm"):
        signal.alarm(TIMEOUT_S)
    print(measure())

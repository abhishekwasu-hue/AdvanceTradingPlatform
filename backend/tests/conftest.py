"""Suite-wide test isolation.

The broker circuit breakers (app/brokers/circuit_breaker.py) are process-wide, time-windowed state:
fake brokers in many worker tests raise NotImplementedError for calls they do not model, and those
failures count towards "3 of 5 calls failed in 60 s". Without a reset, whether a later LIVE test
finds the circuit open depends on how fast the tests before it ran. Each test starts closed.

The global cues (app/ai/global_cues.py) fetch free public market data over the internet; the suite
never does - tests that exercise them turn the flag on and serve the responses themselves.
"""
import os

import pytest

os.environ["GLOBAL_CUES_ENABLED"] = "false"

from app.brokers.circuit_breaker import reset_all


@pytest.fixture(autouse=True)
def _closed_circuit_breakers():
    reset_all()
    yield

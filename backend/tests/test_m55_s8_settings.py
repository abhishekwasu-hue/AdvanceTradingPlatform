"""M55 S8 (A16): operational numbers moved from code constants to settings with defaults equal to the old constants.

Behaviour with the defaults is identical: each setting's default is pinned here to the value the code used before, and
the modules keep their old names bound to the settings. The trigger engine reads its numbers from config, never from
the environment directly.
"""
import ast
import importlib
import importlib.util
from pathlib import Path

import pytest

from app.core import config

APP = Path(__file__).resolve().parents[1] / "app"

# setting -> the constant the code used before M55 S8
OLD_CONSTANTS = {
    "TRIGGER_TICK_SECONDS": 1.0,
    "TRIGGER_TICK_MAX_AGE_SECONDS": 5.0,
    "TRIGGER_STALL_SECONDS": config.WORKER_CYCLE_SECONDS * 3,
    "WORKER_MAX_CONSECUTIVE_FAILURES": 5,
    "WORKER_LOCK_TTL_MIN_SECONDS": 30,
    "EXIT_CLAIM_INFLIGHT_SECONDS": 120,
    "EXIT_ADOPT_CLOCK_SKEW_SECONDS": 5,
    "EXIT_WAIT_ALERT_CHECKS": 5,
    "EXIT_ALERT_COOLDOWN_SECONDS": 1800,
    "STOP_GUARD_ALERT_COOLDOWN_SECONDS": 1800,
    "STOP_GUARD_MAX_EXIT_ATTEMPTS": 3,
    "BROKER_DAILY_CAP_SHARE": 0.9,
    "BROKER_DAILY_ORDER_EXIT_RESERVE_SHARE": 0.1,
}

# (module, old name, setting)
ALIASES = [
    ("app.execution.trigger_engine", "TRIGGER_TICK_SECONDS", "TRIGGER_TICK_SECONDS"),
    ("app.execution.trigger_engine", "TRIGGER_TICK_MAX_AGE_SECONDS", "TRIGGER_TICK_MAX_AGE_SECONDS"),
    ("app.execution.trigger_engine", "TRIGGER_STALL_SECONDS", "TRIGGER_STALL_SECONDS"),
    ("app.workers.trading_worker", "MAX_CONSECUTIVE_FAILURES", "WORKER_MAX_CONSECUTIVE_FAILURES"),
    ("app.trading.position_monitor", "CLAIM_INFLIGHT_SECONDS", "EXIT_CLAIM_INFLIGHT_SECONDS"),
    ("app.trading.position_monitor", "ADOPT_CLOCK_SKEW_SECONDS", "EXIT_ADOPT_CLOCK_SKEW_SECONDS"),
    ("app.trading.position_monitor", "EXIT_WAIT_ALERT_CHECKS", "EXIT_WAIT_ALERT_CHECKS"),
    ("app.trading.position_monitor", "ALERT_COOLDOWN_SECONDS", "EXIT_ALERT_COOLDOWN_SECONDS"),
    ("app.trading.stop_guard", "ALERT_COOLDOWN_SECONDS", "STOP_GUARD_ALERT_COOLDOWN_SECONDS"),
    ("app.trading.stop_guard", "MAX_EXIT_ATTEMPTS", "STOP_GUARD_MAX_EXIT_ATTEMPTS"),
    ("app.brokers.rate_budget", "DAILY_SHARE", "BROKER_DAILY_CAP_SHARE"),
    ("app.brokers.rate_budget", "EXIT_RESERVE_SHARE", "BROKER_DAILY_ORDER_EXIT_RESERVE_SHARE"),
]


def _fresh_config():
    """A separate copy of app/core/config.py read under the current environment (the real module is left alone)."""
    spec = importlib.util.spec_from_file_location("_m55_s8_config_copy", APP / "core" / "config.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", sorted(OLD_CONSTANTS))
def test_each_new_setting_defaults_to_the_old_constant(monkeypatch, name):
    monkeypatch.delenv(name, raising=False)
    fresh = _fresh_config()
    expected = fresh.WORKER_CYCLE_SECONDS * 3 if name == "TRIGGER_STALL_SECONDS" else OLD_CONSTANTS[name]
    assert getattr(fresh, name) == expected
    assert type(getattr(fresh, name)) is type(expected)


@pytest.mark.parametrize(("module", "old", "setting"), ALIASES)
def test_the_old_names_read_the_settings(module, old, setting):
    assert getattr(importlib.import_module(module), old) == getattr(config, setting)


def test_the_worker_lock_ttl_floor_reads_the_setting():
    source = (APP / "workers" / "trading_worker.py").read_text()
    assert "* 2, 30)" not in source
    assert source.count("app_config.WORKER_LOCK_TTL_MIN_SECONDS") == 2


def test_an_out_of_range_value_keeps_the_default(monkeypatch):
    monkeypatch.setenv("STOP_GUARD_MAX_EXIT_ATTEMPTS", "0")
    monkeypatch.setenv("BROKER_DAILY_ORDER_EXIT_RESERVE_SHARE", "0.9")
    fresh = _fresh_config()
    assert fresh.STOP_GUARD_MAX_EXIT_ATTEMPTS == 3 and fresh.BROKER_DAILY_ORDER_EXIT_RESERVE_SHARE == 0.1


def test_trigger_engine_has_no_environment_access():
    tree = ast.parse((APP / "execution" / "trigger_engine.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "os" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "os"
        if isinstance(node, ast.Name):
            assert node.id not in {"os", "environ", "getenv"}
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"environ", "getenv"}

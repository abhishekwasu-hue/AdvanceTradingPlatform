"""The owner's probe CLIs (atp.sh probe-chain / probe-candles) load the tenant data key before reading the broker
credential (24 Oct: both failed with "Data key for tenant 1 is not loaded"; the chain probe hid it as a bad name)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List

import pytest

from app.market_data import probe as candles_probe
from app.option_chain import probe as chain_probe


class _Factory:
    async def __aenter__(self) -> str:
        return "session"

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _wire(monkeypatch: pytest.MonkeyPatch, module: Any, runner: str, result: dict) -> List[Any]:
    calls: List[Any] = []
    owner = SimpleNamespace(tenant_id=7)

    async def fake_owner(session: Any) -> Any:
        return owner

    async def fake_key(session: Any, tenant_id: Any) -> None:
        calls.append(("key", tenant_id))

    async def fake_run(*args: Any, **kwargs: Any) -> dict:
        calls.append(("run",))
        return result

    monkeypatch.setattr("app.db.session._session_factory", lambda: _Factory())
    monkeypatch.setattr("app.secrets_store.envelope.ensure_tenant_key", fake_key)
    monkeypatch.setattr(module, "_owner", fake_owner)
    monkeypatch.setattr(module, runner, fake_run)
    monkeypatch.setattr(module, "format_chain" if module is chain_probe else "format_candles", lambda r: ["ok"])
    return calls


def test_chain_cli_loads_the_tenant_key_first(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = _wire(monkeypatch, chain_probe, "run_chain_probe", {})
    assert chain_probe.main(["NIFTY"]) == 0
    assert calls == [("key", 7), ("run",)]


def test_chain_cli_bad_name_is_still_reported(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = _wire(monkeypatch, chain_probe, "run_chain_probe", {})
    assert chain_probe.main(["!!"]) == 2
    assert "enter an index or stock name" in capsys.readouterr().out
    assert calls == []


def test_chain_cli_does_not_hide_other_errors_as_a_bad_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, chain_probe, "run_chain_probe", {})

    async def boom(session: Any, tenant_id: Any) -> None:
        raise ValueError("Data key for tenant 7 is not loaded")

    monkeypatch.setattr("app.secrets_store.envelope.ensure_tenant_key", boom)
    with pytest.raises(ValueError, match="Data key"):
        chain_probe.main(["NIFTY"])


def test_candles_cli_loads_the_tenant_key_first(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _wire(monkeypatch, candles_probe, "run_candles_probe", {})
    assert candles_probe.main(["candles", "NIFTY 22700 CE 27 OCT 26", "30m", "NFO"]) == 0
    assert calls == [("key", 7), ("run",)]

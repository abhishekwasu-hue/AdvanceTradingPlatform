"""`python -m app.workers.runner_settings show | set` (atp.sh runner-settings): the signal engine runner settings that
the pre-open charts job reads, from the server's command line. Made-up users only."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import delete

from app.db.models import PlatformControlRecord
from app.workers import engine_runner as R
from app.workers import runner_settings as RS
from tests.test_auth_api import _register, _session_factory


def _run(coro):
    return asyncio.run(coro)


def _forget():
    async def go():
        async with _session_factory() as session:
            await session.execute(delete(PlatformControlRecord).where(PlatformControlRecord.key == R.SETTINGS_KEY))
            await session.commit()
    _run(go())


def _do(fn, *args):
    async def go():
        async with _session_factory() as session:
            return await fn(session, *args)
    return _run(go())


def test_index_names_become_atp_lake_keys():
    assert RS.symbols_of("nifty, BANKNIFTY ,") == [{"key": "NSE_INDEX:NIFTY50", "exchange": "NSE", "name": "NIFTY"},
                                                  {"key": "NSE_INDEX:NIFTYBANK", "exchange": "NSE", "name": "BANKNIFTY"}]


def test_show_set_and_show_again_keeps_other_values():
    _forget()
    assert _do(RS.show)[0].startswith("no runner settings stored")
    email = f"runner-{uuid.uuid4().hex[:8]}@example.com"
    _register(email)
    line = _do(RS.set_settings, email.upper(), "NIFTY,BANKNIFTY")
    assert "NSE_INDEX:NIFTY50, NSE_INDEX:NIFTYBANK" in line and email in line

    async def tenant():
        async with _session_factory() as session:
            return list(await R._stored_settings(session))
    key = _run(tenant())[0]

    async def add_value():
        async with _session_factory() as session:
            raw = (await R._stored_settings(session))[key]
            await R.save_settings(session, int(key), {**raw, "delay_seconds": 5}, None)
    _run(add_value())
    _do(RS.set_settings, email, "NIFTY")

    async def stored():
        async with _session_factory() as session:
            return (await R._stored_settings(session))[key]
    raw = _run(stored())
    assert raw["delay_seconds"] == 5 and [s["key"] for s in raw["symbols"]] == ["NSE_INDEX:NIFTY50"]
    shown = _do(RS.show)
    assert len(shown) == 1 and email in shown[0] and "trade_engine OFF" in shown[0] and "preopen_charts OFF" in shown[0]
    _forget()


def test_unknown_user_or_no_symbol_is_not_stored():
    _forget()
    import pytest
    with pytest.raises(R.SettingsProblem):
        _do(RS.set_settings, "nobody-here@example.com", "NIFTY")
    email = f"runner-{uuid.uuid4().hex[:8]}@example.com"
    _register(email)
    with pytest.raises(R.SettingsProblem):
        _do(RS.set_settings, email, " , ")
    assert _do(RS.show)[0].startswith("no runner settings stored")


def test_usage_on_wrong_arguments(capsys):
    assert RS.main(["nope"]) == 2 and "usage:" in capsys.readouterr().out


def test_atp_sh_wires_the_command():
    import re
    from pathlib import Path
    sh = (Path(__file__).resolve().parents[2] / "deploy" / "atp.sh").read_text(encoding="utf-8")
    assert re.search(r"runner-settings\)\s+cmd_runner_settings \"\$@\"", sh)
    assert "dc exec -T backend python -m app.workers.runner_settings" in sh
    assert "#   /opt/atp/deploy/atp.sh runner-settings show" in sh

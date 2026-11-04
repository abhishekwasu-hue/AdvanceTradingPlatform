"""The signal engine runner settings (`platform_controls` row `trade_engine_runner`, engine_runner.SETTINGS_KEY) from the
server's command line - until now they had no reader and no setter (OPEN_QUESTIONS M66Q-1: "admin-set ... no UI yet").
The pre-open charts job (preopen_charts.py) and the signal engine runner read them: whose charts / signals (the user)
and which symbols.

    python -m app.workers.runner_settings show
        Read-only: per organisation, the user's e-mail, the symbols, and whether `trade_engine` / `preopen_charts` are ON.
    python -m app.workers.runner_settings set <e-mail> <SYMBOL,SYMBOL,...>
        Stores that user's organisation's runner settings through engine_runner.save_settings (validated): the user and
        the symbols (index names such as NIFTY,BANKNIFTY become ATP's lake keys); every other stored value of that
        organisation is kept. Turns no flag on or off; places no order.

Operational only: no reading rule, no trading value. Prints no secret (no token, no chat id).
"""
from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select

from app.db.models import User

USAGE = ("usage: python -m app.workers.runner_settings show\n"
         "       python -m app.workers.runner_settings set <e-mail> <SYMBOL,SYMBOL,...>   (e.g. NIFTY,BANKNIFTY)")


def symbols_of(text: str) -> list[dict[str, str]]:
    """"NIFTY,BANKNIFTY" -> [{"key": "NSE_INDEX:NIFTY50", "exchange": "NSE", "name": "NIFTY"}, ...] (ATP's lake keys)."""
    from app.ai.tools.copilot import lake_key
    out: list[dict[str, str]] = []
    for raw in text.split(","):
        name = " ".join(raw.strip().upper().split())
        if not name:
            continue
        key = lake_key(name)
        out.append({"key": key, "exchange": "BSE" if key.startswith("BSE") else "NSE", "name": name})
    return out


async def show(session: Any) -> list[str]:
    from app.platform.controls import flag_enabled
    from app.workers import engine_runner as R
    from app.workers import preopen_charts as P
    stored = await R._stored_settings(session)
    if not stored:
        return ["no runner settings stored (set them with: python -m app.workers.runner_settings set <e-mail> NIFTY,BANKNIFTY)"]
    lines = []
    for tenant_key in sorted(stored, key=str):
        raw = stored[tenant_key] if isinstance(stored[tenant_key], dict) else {}
        try:
            tenant_id = int(tenant_key)
        except ValueError:
            lines.append(f"organisation {tenant_key!r}: not an organisation id")
            continue
        user = await session.get(User, raw.get("user_id")) if isinstance(raw.get("user_id"), int) else None
        keys = ", ".join(str((s or {}).get("key")) for s in raw.get("symbols") or []) or "none"
        flags = " ".join([f"{name} {'ON' if await flag_enabled(session, name, tenant_id) else 'OFF'}" for name in (R.FLAG, P.FLAG)])
        lines.append(f"organisation {tenant_id}: user {user.email if user is not None else 'missing'} · symbols {keys} · {flags}")
    return lines


async def set_settings(session: Any, email: str, symbols: str) -> str:
    from app.workers import engine_runner as R
    user = (await session.scalars(select(User).where(func.lower(User.email) == email.strip().lower()))).first()
    if user is None or not user.is_active:
        raise R.SettingsProblem(f"no active user {email!r}")
    watch = symbols_of(symbols)
    if not watch:
        raise R.SettingsProblem("no symbol given")
    values = dict((await R._stored_settings(session)).get(str(user.tenant_id)) or {})
    values.update(user_id=user.id, symbols=watch)
    await R.save_settings(session, user.tenant_id, values, None)
    return f"organisation {user.tenant_id}: runner settings stored - user {user.email} · symbols {', '.join(w['key'] for w in watch)}"


async def _main(args: list[str]) -> int:
    from app.db.session import _session_factory
    from app.workers import engine_runner as R
    async with _session_factory() as session:
        if args == ["show"]:
            print("\n".join(await show(session)))
            return 0
        if len(args) == 3 and args[0] == "set":
            try:
                print(await set_settings(session, args[1], args[2]))
            except R.SettingsProblem as exc:
                print(f"not stored: {exc}")
                return 1
            return 0
    print(USAGE)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_main(list(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    sys.exit(main())

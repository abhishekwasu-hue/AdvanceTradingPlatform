"""Runner S5: the keystore and broker-login commands, on the user's own runner.

    python -m app.runner.keys init [--mode keyfile|scrypt]   create the keystore (default: a key file, for unattended starts)
    python -m app.runner.keys add angel_one                  enter the broker details (secrets are asked without echo)
    python -m app.runner.keys rotate angel_one [--field pin] change some details (Enter keeps a value as it is)
    python -m app.runner.keys remove angel_one               delete the broker's details and session
    python -m app.runner.keys list                           which brokers / fields are set (names only, never a value)
    python -m app.runner.keys rekey [--mode keyfile|scrypt]  re-encrypt under a new key file or passphrase
    python -m app.runner.keys login angel_one                log in now (also after the day's automatic login gave up)
    python -m app.runner.keys status angel_one               the line the heartbeat reports

All take `--home <folder>` (default $ATP_RUNNER_HOME or ~/.atp-runner, the S2 runner folder). Nothing typed here is
echoed, printed, logged or sent to ATP. Once the S2 entry point (`python -m app.runner`) is merged it dispatches its
`keys` command here. The prompts are parameters so tests drive them without a terminal.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from typing import Callable, Optional

from app.runner.keystore import BROKER_FIELDS, KEYFILE, REQUIRED_FIELDS, SCRYPT, SECRET_FIELDS, Keystore, KeystoreError

LABELS = {"api_key": "SmartAPI app key (API key)", "client_id": "Client code", "pin": "Trading PIN",
          "totp_secret": "TOTP secret (the key shown when you enabled TOTP for SmartAPI)",
          "public_ip": "This machine's static IP registered with Angel One (optional)"}


def _ask(field: str, secret_prompt: Callable[[str], str], plain_prompt: Callable[[str], str], keep: bool) -> str:
    label = LABELS.get(field, field) + (" [Enter keeps it]" if keep else "") + ": "
    return (secret_prompt if field in SECRET_FIELDS else plain_prompt)(label)


def _new_passphrase(secret_prompt: Callable[[str], str]) -> str:
    first = secret_prompt("New keystore passphrase: ")
    if first != secret_prompt("Repeat the passphrase: "):
        raise KeystoreError("The two passphrases differ")
    return first


def main(argv: Optional[list[str]] = None, *, secret_prompt: Callable[[str], str] = getpass.getpass,
         plain_prompt: Callable[[str], str] = input, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.runner.keys", description="Runner keystore and broker login")
    parser.add_argument("command", choices=["init", "add", "rotate", "remove", "list", "rekey", "login", "status"])
    parser.add_argument("broker", nargs="?")
    parser.add_argument("--home", default=os.environ.get("ATP_RUNNER_HOME", "~/.atp-runner"))
    parser.add_argument("--mode", choices=[KEYFILE, SCRYPT], default=KEYFILE)
    parser.add_argument("--field", action="append", default=[], help="rotate: only this field (repeatable)")
    args = parser.parse_args(argv)
    if args.command in ("add", "rotate", "remove", "login", "status") and not args.broker:
        parser.error(f"{args.command}: name the broker (angel_one)")

    store = Keystore(args.home, passphrase=lambda: secret_prompt("Keystore passphrase: "))
    try:
        if args.command == "init":
            store.init(args.mode, _new_passphrase(secret_prompt) if args.mode == SCRYPT else None)
            out(f"Keystore created ({args.mode})")
        elif args.command == "rekey":
            store.mode()                                    # opens / checks the existing one first
            store.brokers()
            store.rekey(args.mode, _new_passphrase(secret_prompt) if args.mode == SCRYPT else None)
            out(f"Keystore re-encrypted ({args.mode})")
        elif args.command == "add":
            fields = BROKER_FIELDS.get(args.broker)
            if fields is None:
                raise KeystoreError(f"The runner keeps details for: {', '.join(sorted(BROKER_FIELDS))}")
            values = {f: _ask(f, secret_prompt, plain_prompt, keep=False) for f in fields}
            store.put_broker(args.broker, values, replace=True)
            out(f"{args.broker}: details saved ({', '.join(f for f in fields if values.get(f, '').strip())}); "
                f"to log in now: python -m app.runner.keys login {args.broker}")
        elif args.command == "rotate":
            fields = tuple(args.field) or BROKER_FIELDS.get(args.broker, ())
            store.broker_fields(args.broker)                # must exist
            values = {f: _ask(f, secret_prompt, plain_prompt, keep=True) for f in fields}
            store.put_broker(args.broker, values, replace=False)
            changed = [f for f, v in values.items() if v.strip()]
            out(f"{args.broker}: " + (f"changed {', '.join(changed)}" if changed else "nothing changed"))
        elif args.command == "remove":
            out(f"{args.broker}: " + ("details removed" if store.remove_broker(args.broker) else "nothing stored"))
        elif args.command == "list":
            brokers = store.brokers()
            out("\n".join(f"{b}: {', '.join(f) or '-'}" for b, f in sorted(brokers.items())) or "No broker details yet")
            missing = {b: [f for f in REQUIRED_FIELDS.get(b, ()) if f not in fs] for b, fs in brokers.items()}
            for b, m in missing.items():
                if m:
                    out(f"{b}: missing {', '.join(m)}")
        elif args.command in ("login", "status"):
            from app.runner.broker_login import DailyLogin
            daily = DailyLogin(args.home, store, args.broker)
            if args.command == "login":
                asyncio.run(daily.login(manual=True))
            from datetime import datetime, timezone
            out(daily.status_line(datetime.now(timezone.utc)))
            return 0 if daily.status().get("state") == "logged_in" or args.command == "status" else 1
    except KeystoreError as exc:
        out(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]

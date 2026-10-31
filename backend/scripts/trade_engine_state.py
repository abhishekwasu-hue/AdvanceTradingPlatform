#!/usr/bin/env python
"""M66 EN-1: the signal engine (atp_private.trade_engine) as this release's loader sees it. Read-only; one JSON line.

    python scripts/trade_engine_state.py

    {"installed": true, "name": "...", "version": "...", "tag": "...", "interface": 1}   the private package loaded
    {"installed": false, "reason": "the private package is not ..."}                     why not

`deploy/atp.sh health` prints "trade engine: OK (<name> <version> tag <tag>)" or "MISSING (<reason>)" from it. It goes
through app/engine_provider/loader.py, the same loader the API uses, and never prints any of the engine's content.
Analyze a chart's engine has its own script (scripts/engine_status.py), unchanged.
"""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    logging.disable(logging.CRITICAL)          # one JSON line on stdout, nothing else
    from app.engine_provider import loader

    state = loader.describe(admin=True)
    if state["installed"]:
        out = {"installed": True, "name": str(state["name"]), "version": str(state["version"]), "tag": str(state["tag"]),
               "interface": int(state["interface"])}
    else:
        out = {"installed": False, "reason": str(state["reason"])}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

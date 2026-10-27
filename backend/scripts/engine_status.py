#!/usr/bin/env python
"""A1 (UI-H bug lane): the chart analysis engine as this release's loader sees it. Read-only; one JSON line.

    python scripts/engine_status.py

    {"installed": true, "name": "...", "version": "..."}            the private package loaded
    {"installed": false, "reason": "the private package is not ..."} why not (a module or exception class name at most)

`deploy/atp.sh health` prints "chart analysis engine: OK (<name> <version>)" or "MISSING (<reason>)" from it. It goes
through app/chart_analyze/provider.py, the same loader the API uses, and never prints any of the engine's text.
"""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    logging.disable(logging.CRITICAL)          # one JSON line on stdout, nothing else
    from app.chart_analyze import provider

    state = provider.describe(admin=True)
    if state["installed"]:
        out = {"installed": True, "name": str(state["name"]), "version": str(state["version"])}
    else:
        out = {"installed": False, "reason": str(state["reason"])}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

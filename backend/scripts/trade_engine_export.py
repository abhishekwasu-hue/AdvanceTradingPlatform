#!/usr/bin/env python
"""M79 W10: one organisation's signal-engine output of one India-time day, as JSON lines, for the shadow run's daily
comparison with Trade (run by the owner on the host; the file stays on the host). Read-only.

    python scripts/trade_engine_export.py --tenant <organisation id> --day YYYY-MM-DD > atp-YYYY-MM-DD.jsonl

Lines: kind "signal" (the runner's internal signal row: model, area, entry zone, invalidation, evidence; written_at)
and kind "run" (each bar's final run record: status, reason, data problems). See app/workers/engine_runner.export_day.
The count goes to stderr. Nothing is written to the database.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def export(tenant_id: int, day: date) -> list:
    from app.db.session import _session_factory
    from app.workers import engine_runner as R

    async with _session_factory() as session:
        return await R.export_day(session, tenant_id, day)


def main(argv=None) -> int:
    logging.disable(logging.CRITICAL)
    p = argparse.ArgumentParser(description="Signal-engine output of one day (JSON lines).")
    p.add_argument("--tenant", type=int, required=True)
    p.add_argument("--day", type=date.fromisoformat, required=True)
    a = p.parse_args(argv)
    lines = asyncio.run(export(a.tenant, a.day))
    for line in lines:
        sys.stdout.write(json.dumps(line, default=str) + "\n")
    print(f"{sum(1 for x in lines if x['kind'] == 'signal')} signal(s), {sum(1 for x in lines if x['kind'] == 'run')} run record(s)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Z10-a: lists saved screens and instrument alert rules that no longer pass the ScreenQL validator. Read-only.

    docker compose exec backend python scripts/audit_screens.py [--json]

Exit 0 when everything validates, 1 when something is listed (tell its owner; the bar-close engine skips such a rule
and says why). The same report is GET /api/admin/screener/audit.
"""
import argparse
import asyncio
import json
import sys


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    from app.db.session import _session_factory
    from app.screener.audit import audit_stored

    async with _session_factory() as session:
        report = await audit_stored(session)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"checked {report['screens_checked']} screens and {report['alert_rules_checked']} instrument alert rules; "
              f"{len(report['failing'])} no longer validate")
        for row in report["failing"]:
            extra = f" (alert rules {row['alert_rules']})" if row.get("alert_rules") else ""
            print(f"- {row['kind']} #{row['id']} tenant {row['tenant_id']} {row['name']!r}{extra}: {row['text']}")
            for problem in row["problems"]:
                print(f"    {problem}")
    return 1 if report["failing"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

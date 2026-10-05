#!/usr/bin/env python
"""Phase AX: the first-PAPER-day check (docs/GO_LIVE_MR.md step 4). Read-only.

    docker compose exec backend python scripts/first_paper_day_check.py [--tenant <id|email>]
                                      [--no-smoke] [--send-test-alert] [--json] [--lang mr|en]

Reads the platform and organisation go-live checklists, today's NSE session, runs the read-only
broker smoke test on every stored session with a VALID token (never an order), checks that the
active PAPER deployments are being evaluated, and - only with --send-test-alert - sends one test
message through each alert channel. Prints ✅ / ⚠️ / ❌ lines with the fix for each, and exits 0
when there is no ❌, 1 when there is, 2 when no organisation could be chosen.
No secret is read, printed or needed: it runs with the backend's own environment.
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only readiness check for the first PAPER trading day.")
    parser.add_argument("--tenant", help="organisation id or a member's email (default: the one with an active deployment)")
    parser.add_argument("--no-smoke", action="store_true", help="skip the read-only broker probes")
    parser.add_argument("--send-test-alert", action="store_true", help="send one test message through each configured alert channel")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--lang", choices=("mr", "en"), default="mr")
    return parser


async def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from app.db.session import _session_factory
    from app.platform import first_day

    async with _session_factory() as session:
        tenant, user = await first_day.pick_tenant(session, args.tenant)
        if tenant is None or user is None:
            print("No organisation chosen: pass --tenant <id|email> (several organisations have active deployments, or none exists).", file=sys.stderr)
            return 2
        report = await first_day.run(session, tenant, user, smoke=not args.no_smoke, send_test_alert=args.send_test_alert)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) if args.json else report.render(args.lang))
    return 0 if report.ready else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

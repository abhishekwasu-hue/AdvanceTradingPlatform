#!/usr/bin/env python
"""Production readiness A2: one run of the operator's server checks (app/observability/ops_alerts.py).

    python scripts/ops_check.py --host-facts -      # the host facts as one JSON object on stdin (atp.sh ops-check)
    python scripts/ops_check.py                     # app checks only; the stored host facts are used while fresh

`deploy/atp.sh ops-check` runs it every 5 minutes inside the backend container, from the crontab block `deploy`
installs. It reads the API's and the worker's Prometheus series inside the compose network, judges every check, sends
what changed to the operator's Telegram chat / e-mail (dedup in `platform_controls`) and prints one summary line:
states and counts only, never a secret, a chat id or an address. Exit 0 whenever the checks ran (a FAIL is an alert,
not a failed cron job); 1 when they could not run (the database is down - the outside uptime check covers that).
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run_once(host_facts):
    from app.core import config
    from app.db.session import _session_factory
    from app.observability import ops_alerts

    api_metrics = await ops_alerts.fetch_metrics(config.OPS_API_METRICS_URL)
    worker_metrics = await ops_alerts.fetch_metrics(config.OPS_WORKER_METRICS_URL)
    async with _session_factory() as session:
        return await ops_alerts.run(session, host_facts=host_facts, source="server", api_metrics=api_metrics, worker_metrics=worker_metrics)


def summary(result) -> str:
    states = " ".join(f"{c['key']}={c['state']}" for c in result["checks"])
    sent = result["sent"]
    delivery = ",".join(f"{k}:{'sent' if v else 'failed'}" for k, v in result["delivery"].items() if v is not None) or "no channel"
    return f"ops-check: {states} | {len(sent)} change(s) to report" + (f" ({delivery})" if sent else "")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host-facts", help="'-' = read the host facts JSON from stdin")
    args = parser.parse_args(argv)
    host_facts = None
    if args.host_facts == "-":
        raw = sys.stdin.read().strip()
        try:
            host_facts = json.loads(raw) if raw else {}
        except ValueError:
            host_facts = {}
        if not isinstance(host_facts, dict):
            host_facts = {}
    try:
        result = asyncio.run(run_once(host_facts))
    except Exception as exc:  # noqa: BLE001 - one line for the cron log, no traceback with values in it
        print(f"ops-check: could not run ({type(exc).__name__})")
        return 1
    print(summary(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

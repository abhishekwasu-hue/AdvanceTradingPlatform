#!/usr/bin/env python3
"""The real-host load test's verdict and report (`deploy/atp.sh loadtest`, docs/deploy/LOADTEST_REAL_HOST.md).

Stdlib only; runs on the host with its python3. Reads what the run left in its state directory and writes one Markdown
file for the owner to paste back, and prints the same verdict in plain text:

    python3 deploy/hostinger/loadtest_summary.py --dir <run state dir> --out /opt/atp/reports/loadtest-<stamp>.md

Inputs in --dir (each may be missing - an interrupted run still gets a report of what it has):
    meta.json          the run's settings and the safety checks atp.sh made (flags / organisations unchanged)
    result.json        the merged load result (backend/scripts/loadtest.py --merge --json-out)
    docker-stats.csv   epoch,container,cpu%,mem usage,mem% - every few seconds (docker stats --no-stream)
    host-memory.csv    epoch,MemTotal,MemFree,MemAvailable,SwapTotal,SwapFree (kB),pswpin,pswpout (pages) - /proc
    cleanup.json       backend/scripts/loadtest_org.py cleanup
    metrics-before.prom / metrics-after.prom   the API's /metrics before and after: time per route and its waits, as
                       the CI's load runs report them (deploy/ci/metrics_summary.py, reused)

The SLOs (owner decisions, docs/OPEN_QUESTIONS.md LD-9): every API scenario p95 <= 500 ms; login measured apart,
p95 <= 2,000 ms with all users logged in; zero errors (server errors, transport failures, other 4xx and rate-limit
refusals). A scenario the deployment answers only with "no broker session" (409) or "feature off" is reported as not
measured, never as passed. Exit 0 = every SLO met and the cleanup verified, 1 = an SLO missed (or no load result),
4 = SLOs met but the cleanup not verified.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LOGIN_P95_MS = 2000.0
API_P95_MS = 500.0
ERROR_OUTCOMES = ("server_error", "transport", "client_error", "throttled")
ANSWERED = ("ok", "ok_empty")
NOT_JUDGED = {"unavailable": "409 no broker session (none exists in the load-test organisation, by design)",
              "disabled": "feature switched off on this deployment"}
UNITS = {"b": 1, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4, "kb": 1000, "mb": 1000 ** 2,
         "gb": 1000 ** 3, "tb": 1000 ** 4}
SCENARIO_NOTES = {
    "chart": "POST /api/market-data/candles - broker candles through the user's own session",
    "chart_lake": "GET /api/market-data/history - a year of daily bars from the shared market lake (database only, no broker)",
    "screener_run": "POST /api/screener/run (preview) - bars through the user's own broker session",
}


def limit_for(scenario: str) -> float:
    return LOGIN_P95_MS if scenario == "login" else API_P95_MS


def to_bytes(text: str) -> Optional[float]:
    m = re.fullmatch(r"\s*([0-9.]+)\s*([A-Za-z]+)\s*", text or "")
    if not m or m.group(2).lower() not in UNITS:
        return None
    return float(m.group(1)) * UNITS[m.group(2).lower()]


def mib(value: Optional[float]) -> str:
    return "-" if value is None else f"{value / 1024 ** 2:,.0f}"


def _load_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# --- SLOs ---------------------------------------------------------------------------------------------------------

def judge(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Per scenario: verdict (PASS / FAIL / NOT MEASURED), errors, refusals; and the overall SLO verdict with reasons."""
    out: Dict[str, Any] = {"scenarios": {}, "errors": 0, "reasons": [], "not_measured": [], "slo_pass": False}
    if not result or not result.get("requests"):
        out["reasons"].append("no load result (the run did not finish, or sent no request)")
        return out
    for name, s in (result.get("scenarios") or {}).items():
        outcomes = {k: int(v) for k, v in (s.get("outcomes") or {}).items()}
        answered = sum(outcomes.get(k, 0) for k in ANSWERED)
        errors = sum(outcomes.get(k, 0) for k in ERROR_OUTCOMES)
        limit = limit_for(name)
        if not answered:
            verdict = "NOT MEASURED"
            why = "; ".join(NOT_JUDGED.get(k, k) for k in outcomes if k not in ERROR_OUTCOMES) or "no answer"
            out["not_measured"].append(f"{name}: {why}")
        else:
            verdict = "PASS" if float(s["p95_ms"]) <= limit else "FAIL"
            if verdict == "FAIL":
                out["reasons"].append(f"{name} p95 {float(s['p95_ms']):,.0f} ms is over {limit:,.0f} ms")
        if errors:
            out["reasons"].append(f"{name}: {errors} error(s) ({', '.join(f'{k} {outcomes[k]}' for k in ERROR_OUTCOMES if outcomes.get(k))})")
        out["errors"] += errors
        out["scenarios"][name] = {"verdict": verdict, "limit_ms": limit, "errors": errors, "answered": answered,
                                  "empty": outcomes.get("ok_empty", 0), "outcomes": outcomes,
                                  **{k: s.get(k) for k in ("n", "p50_ms", "p95_ms", "p99_ms")}}
    sessions = result.get("sessions") or {}
    if "login" not in out["scenarios"] or out["scenarios"]["login"]["verdict"] == "NOT MEASURED":
        out["reasons"].append("login was not measured")
    if sessions.get("no_login"):
        out["reasons"].append(f"{sessions['no_login']} of {result.get('users')} users could not log in")
    out["slo_pass"] = not out["reasons"]
    return out


# --- memory and CPU (LAB-6) ---------------------------------------------------------------------------------------

def container_stats(path: Path, client_prefix: str) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """(per container: mem mean / peak / limit, CPU mean / peak; the load client's shards summed per sample)."""
    per: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    client_cpu: Dict[str, float] = defaultdict(float)
    client_mem: Dict[str, float] = defaultdict(float)
    shard_cpu: List[float] = []
    try:
        rows = list(csv.reader(path.read_text().splitlines()))
    except OSError:
        rows = []
    for row in rows[1:]:
        if len(row) < 4:
            continue
        stamp, name, cpu_text, usage = row[0], row[1], row[2], row[3]
        try:
            cpu = float(cpu_text.strip().rstrip("%"))
        except ValueError:
            continue
        used_text, _, limit_text = usage.partition("/")
        used, limit = to_bytes(used_text), to_bytes(limit_text)
        if used is None:
            continue
        if client_prefix and name.startswith(client_prefix):
            client_cpu[stamp] += cpu
            client_mem[stamp] += used
            shard_cpu.append(cpu)
            continue
        per[name]["cpu"].append(cpu)
        per[name]["mem"].append(used)
        if limit:
            per[name]["limit"].append(limit)
    containers = {}
    for name, v in sorted(per.items()):
        containers[name] = {"samples": len(v["mem"]), "mem_mean": statistics.fmean(v["mem"]), "mem_peak": max(v["mem"]),
                            "limit": max(v["limit"]) if v["limit"] else None,
                            "cpu_mean": statistics.fmean(v["cpu"]), "cpu_peak": max(v["cpu"])}
    client = {"samples": len(client_cpu), "shard_samples": shard_cpu}
    if client_cpu:
        client.update(cpu_mean=statistics.fmean(client_cpu.values()), cpu_peak=max(client_cpu.values()),
                      mem_mean=statistics.fmean(client_mem.values()), mem_peak=max(client_mem.values()))
    return containers, client


def host_memory(path: Path) -> Dict[str, Any]:
    try:
        rows = [r for r in csv.reader(path.read_text().splitlines())][1:]
    except OSError:
        rows = []
    vals = []
    for r in rows:
        try:
            vals.append([float(x) for x in r[:8]])
        except (ValueError, IndexError):
            continue
    if not vals:
        return {}
    kb = 1024.0
    total = vals[-1][1] * kb
    avail = [v[3] * kb for v in vals]
    free = [v[2] * kb for v in vals]
    swap_used = [(v[4] - v[5]) * kb for v in vals]
    return {"samples": len(vals), "total": total, "available_min": min(avail), "available_mean": statistics.fmean(avail),
            "used_peak": total - min(avail), "free_min": min(free), "swap_total": vals[-1][4] * kb,
            "swap_used_peak": max(swap_used), "swap_used_start": swap_used[0],
            "swap_in_mib": (vals[-1][6] - vals[0][6]) * 4096 / 1024 ** 2, "swap_out_mib": (vals[-1][7] - vals[0][7]) * 4096 / 1024 ** 2}


def server_side(run_dir: Path) -> str:
    """The API's own view of the run (deploy/ci/metrics_summary.py over the two /metrics scrapes), or ""."""
    before, after = run_dir / "metrics-before.prom", run_dir / "metrics-after.prom"
    if not (before.is_file() and after.is_file() and before.stat().st_size and after.stat().st_size):
        return ""
    import contextlib
    import io
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
    try:
        import metrics_summary
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            metrics_summary.main(str(before), str(after))
        return buf.getvalue().strip()
    except Exception as exc:  # noqa: BLE001 - a diagnostic; the verdict does not depend on it
        return f"(not available: {type(exc).__name__})"
    finally:
        sys.path.pop(0)


# --- the report ---------------------------------------------------------------------------------------------------

def build(run_dir: Path) -> Tuple[str, str, int]:
    meta = _load_json(run_dir / "meta.json") or {}
    result = _load_json(run_dir / "result.json")
    cleanup = _load_json(run_dir / "cleanup.json")
    verdict = judge(result)
    prefix = meta.get("client_prefix") or ""
    containers, client = container_stats(run_dir / "docker-stats.csv", prefix)
    host = host_memory(run_dir / "host-memory.csv")
    vcpus = float(meta.get("vcpus") or 2)
    cap = float(meta.get("client_cpus_per_shard") or 0)
    clean = bool(cleanup and cleanup.get("clean"))

    slo_line = ("PASS - every measured SLO met" if verdict["slo_pass"] else "FAIL - " + "; ".join(verdict["reasons"]))
    cleanup_line = ("verified clean" if clean else "NOT VERIFIED - see the Cleanup section and the runbook's manual cleanup"
                    if cleanup else "NOT RUN or no report - run: /opt/atp/deploy/atp.sh loadtest-cleanup " + str(meta.get("stamp", "<stamp>")))
    md: List[str] = [f"# Load test on the production host - {meta.get('stamp', '?')}", "",
                     f"**SLO verdict: {slo_line}**", "", f"**Cleanup: {cleanup_line}**", ""]
    if meta.get("market") and not str(meta["market"]).startswith("open"):
        md += [f"> Run outside NSE market hours ({meta['market']}): these numbers are not the market-hours result.", ""]
    if verdict["not_measured"]:
        md += ["Not measured (reported, never counted as passed):", ""] + [f"- {x}" for x in verdict["not_measured"]] + [""]

    md += ["## Setup", "", "| What | Value |", "|---|---|"]
    for key, label in (("started_ist", "Started (IST)"), ("release", "Release"), ("host", "Host"), ("market", "Market"),
                       ("users", "Users (sessions)"), ("ramp", "Login ramp (s)"), ("seconds", "After the ramp (s)"),
                       ("think", "Think time (s)"), ("shards", "Client shards (one client IP each)"),
                       ("client_cpus_per_shard", "Client CPU cap per shard (CPUs)"), ("edge", "Edge"), ("chart_data", "Chart data")):
        if meta.get(key) not in (None, ""):
            md.append(f"| {label} | {meta[key]} |")
    md.append("")

    md += ["## SLOs", ""]
    for name, s in verdict["scenarios"].items():
        if s["verdict"] == "NOT MEASURED":
            md.append(f"- {name}: NOT MEASURED ({', '.join(f'{k} {v}' for k, v in sorted(s['outcomes'].items()))})")
        else:
            empty = f"; {s['empty']} answer(s) without bars" if s["empty"] else ""
            md.append(f"- {name}: {s['verdict']} - p95 {float(s['p95_ms']):,.0f} ms against {s['limit_ms']:,.0f} ms, "
                      f"{s['errors']} error(s){empty}")
    if result:
        sess = result.get("sessions") or {}
        md.append(f"- sessions: {sess.get('logged_in', 0)} of {result.get('users')} logged in")
        md.append(f"- errors in total: {verdict['errors']} of {result.get('requests', 0):,} requests "
                  f"({result.get('rps', 0)} requests/s over {result.get('seconds', 0):.0f} s)")
    md.append("")

    if verdict["scenarios"]:
        md += ["## Latency (ms)", "", "| Scenario | n | p50 | p95 | p99 | p95 limit | verdict |", "|---|---|---|---|---|---|---|"]
        for name, s in verdict["scenarios"].items():
            md.append(f"| {name} | {s['n']} | {s['p50_ms']} | {s['p95_ms']} | {s['p99_ms']} | {s['limit_ms']:,.0f} | {s['verdict']} |")
        md += ["", "## Outcomes (errors and refusals)", "", "| Scenario | ok | no bars | errors | refused (not judged) |", "|---|---|---|---|---|"]
        for name, s in verdict["scenarios"].items():
            o = s["outcomes"]
            err = ", ".join(f"{k} {o[k]}" for k in ERROR_OUTCOMES if o.get(k)) or "0"
            ref = ", ".join(f"{k} {o[k]}" for k in NOT_JUDGED if o.get(k)) or "-"
            md.append(f"| {name} | {o.get('ok', 0)} | {o.get('ok_empty', 0)} | {err} | {ref} |")
        md.append("")
    if result and result.get("after_ramp"):
        md += ["After the login ramp only (diagnostic; the verdict covers every request):", "", "| Scenario | n | p50 | p95 |", "|---|---|---|---|"]
        md += [f"| {k} | {v['n']} | {v['p50_ms']} | {v['p95_ms']} |" for k, v in result["after_ramp"].items()]
        md.append("")

    md += ["## Memory (LAB-6)", "", "Per container, from `docker stats` (MEM USAGE: the container's memory without its file cache) "
           f"every {meta.get('sample_seconds', '?')} s:", "",
           "| Container | mean MiB | peak MiB | limit MiB | peak % of limit | CPU % mean | CPU % peak |", "|---|---|---|---|---|---|---|"]
    limits_total = 0.0
    for name, c in containers.items():
        pct = f"{100 * c['mem_peak'] / c['limit']:.0f}%" if c["limit"] else "-"
        if c["limit"] and c["limit"] < host.get("total", float("inf")):
            limits_total += c["limit"]
        md.append(f"| {name} | {mib(c['mem_mean'])} | {mib(c['mem_peak'])} | {mib(c['limit'])} | {pct} | {c['cpu_mean']:.0f} | {c['cpu_peak']:.0f} |")
    if not containers:
        md.append("| (no samples) | | | | | | |")
    md.append("")
    if host:
        md += ["Host (`/proc/meminfo`, `/proc/vmstat`):", "", "| Measure | MiB |", "|---|---|",
               f"| MemTotal | {mib(host['total'])} |", f"| MemAvailable, lowest | {mib(host['available_min'])} |",
               f"| MemAvailable, mean | {mib(host['available_mean'])} |", f"| Used (total - available), peak | {mib(host['used_peak'])} |",
               f"| MemFree, lowest | {mib(host['free_min'])} |", f"| Swap total | {mib(host['swap_total'])} |",
               f"| Swap used, at start | {mib(host['swap_used_start'])} |", f"| Swap used, peak | {mib(host['swap_used_peak'])} |",
               f"| Swapped in during the run | {host['swap_in_mib']:,.1f} |", f"| Swapped out during the run | {host['swap_out_mib']:,.1f} |"]
        if limits_total:
            md.append(f"| Sum of the containers' memory limits (sampled ones) | {mib(limits_total)} |")
        md.append("")

    md += ["## Load generator", ""]
    if client.get("samples"):
        over = sum(1 for c in client["shard_samples"] if cap and c >= 90 * cap)
        md += [f"The client ran on this host ({meta.get('shards', '?')} containers, one client IP each, CPU weight below the app's). "
               "Its own use, all shards together:", "",
               f"- CPU: mean {client['cpu_mean']:.0f}% / peak {client['cpu_peak']:.0f}% of one vCPU "
               f"({client['cpu_mean'] / vcpus:.0f}% / {client['cpu_peak'] / vcpus:.0f}% of the box's {vcpus:.0f} vCPUs)",
               f"- Memory: mean {mib(client['mem_mean'])} MiB / peak {mib(client['mem_peak'])} MiB",
               f"- Shard samples at 90% or more of the per-shard CPU cap: {over} of {len(client['shard_samples'])}"
               + (" - the client itself may have added latency" if over else ""), ""]
    else:
        md += ["No client samples.", ""]

    md += ["## Cleanup", ""]
    if cleanup:
        tomb = cleanup.get("tombstone") or {}
        md += [f"- Organisation: {cleanup.get('tenant')} - "
               + (f"kept as a tombstone (status {tomb.get('status')}, {tomb.get('users_kept')} users, {tomb.get('active_users')} active, "
                  f"{tomb.get('usable_passwords')} with a usable password)" if tomb else "removed entirely"),
               "- Deleted: " + (", ".join(f"{k} {v}" for k, v in sorted(cleanup.get("deleted", {}).items())) or "nothing"),
               "- Kept on purpose (append-only ledgers; the audit chain is never edited): "
               + (", ".join(f"{k} {v}" for k, v in sorted(cleanup.get("ledger_rows_kept", {}).items())) or "none"),
               "- Left over (should be none): " + (", ".join(f"{k} {v}" for k, v in sorted(cleanup.get("remaining", {}).items())) or "none"),
               ]
        if cleanup.get("failed"):
            md.append("- Failed deletes: " + "; ".join(f"{k}: {v}" for k, v in cleanup["failed"].items()))
        if cleanup.get("audit_chain"):
            md.append(f"- Audit chain (from its latest anchor): {cleanup['audit_chain']}")
        md.append(f"- Verified: {'yes' if clean else 'NO'}")
    else:
        md.append("- No cleanup report: run the manual cleanup (docs/deploy/LOADTEST_REAL_HOST.md).")
    for key, label in (("load_containers_left", "Load-client containers left"), ("accounts_file_removed", "Accounts file removed")):
        if key in meta:
            md.append(f"- {label}: {meta[key]}")
    md.append("")

    md += ["## Safety checks", ""]
    for key, label in (("posture", "Deployment"), ("flags_unchanged", "Feature flags unchanged by the run"),
                       ("orgs_unchanged", "Other organisations and their users unchanged by the run"),
                       ("broker_credentials", "Broker credentials in the load-test organisation")):
        if key in meta:
            md.append(f"- {label}: {meta[key]}")
    md += ["", "## How the charts got data", "",
           "- `chart` (" + SCENARIO_NOTES["chart"] + "): the load-test organisation has no broker session, so this answers 409 "
           "before any broker call. It is reported as not measured. If LD-10 lets a chart read shared candles without a session of "
           "its own, the same scenario is measured with no change here.",
           "- `chart_lake` (" + SCENARIO_NOTES["chart_lake"] + "): the chart data such an organisation can see. Answers without bars "
           "(an empty lake for a symbol) are counted apart in the outcomes table.",
           "- `screener_run`: like `chart`, through the user's own broker session - 409 here.", ""]
    side = server_side(run_dir)
    if side:
        md += ["## Server side (the API's own metrics)", "", side, ""]
    text = "\n".join(md) + "\n"

    lines = [f"LOAD TEST {meta.get('stamp', '?')} - SLO: {slo_line}", f"Cleanup: {cleanup_line}"]
    for name, s in verdict["scenarios"].items():
        lines.append(f"  {name:32} {s['verdict']:12} p50 {s['p50_ms']!s:>8} p95 {s['p95_ms']!s:>8} p99 {s['p99_ms']!s:>8} ms "
                     f"(limit {s['limit_ms']:,.0f})  errors {s['errors']}")
    peaks = ", ".join(f"{n} {mib(c['mem_peak'])}" for n, c in containers.items())
    lines.append(f"Memory peaks (MiB): {peaks or 'no samples'}")
    if host:
        lines.append(f"Host: MemAvailable lowest {mib(host['available_min'])} of {mib(host['total'])} MiB, swap used peak "
                     f"{mib(host['swap_used_peak'])} MiB (swapped in {host['swap_in_mib']:.1f} / out {host['swap_out_mib']:.1f} MiB)")
    if client.get("samples"):
        lines.append(f"Load client CPU: mean {client['cpu_mean']:.0f}% / peak {client['cpu_peak']:.0f}% of one vCPU")
    code = 0 if verdict["slo_pass"] and clean else 1 if not verdict["slo_pass"] else 4
    return text, "\n".join(lines), code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", required=True, help="the run's state directory")
    parser.add_argument("--out", required=True, help="the Markdown report to write")
    args = parser.parse_args()
    text, terminal, code = build(Path(args.dir))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(terminal)
    print(f"Report: {out}")
    return code


if __name__ == "__main__":
    sys.exit(main())

"""Signal reports (JSON, CSV, XLSX, PNG, PDF) built from the stored canonical signal record.

* Owner-only: the record is loaded with `store.get_signal(key, user_id)`; another user's key yields nothing.
* The chart is re-computed from currently available validated data. If the data fingerprint differs from the
  `data_snapshot` stored with the signal, the report says so explicitly (the signal record itself is never changed).
* Files are written under `data/reports/<user_id>/` (0700 data dir) with safe generated names; each generation is logged
  in the `reports` table with status, format, snapshot match and size.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..config import ROOT
from .chart_png import render_chart

FORMATS = ("json", "csv", "xlsx", "png", "pdf")
MODES = ("quick", "standard", "detailed")
SNAP_TEXT = {True: "identical to the signal's data",
             False: "DIFFERENT — the provider's data changed since the signal; chart shows current data up to the signal bar",
             None: "not verifiable"}
DISCLAIMER = ("Technical analysis only — not investment advice and not an order. Scores are rule-based confluence measures, "
              "not probabilities of success. Market data may be delayed; verify before acting.")


@dataclass
class ReportContext:
    record: dict            # stored signal row (with payload)
    payload: dict
    analysis: object | None
    quality: object | None
    snapshot_match: bool | None
    generated_utc: str
    events: list = dc_field(default_factory=list)      # append-only lifecycle events AFTER the original record (A-005)


def _conditions(payload: dict) -> list[dict]:
    out = []
    for status, field in (("passed", "conditions_passed"), ("failed", "conditions_failed"), ("not evaluable", "conditions_na")):
        for part in [p.strip() for p in str(payload.get(field) or "").split(" | ") if p.strip()]:
            mand = part.startswith("[M] ")
            part = part[4:] if mand else part
            name, _, detail = part.partition(": ")
            out.append({"condition": name, "result": status, "mandatory": mand or None, "detail": detail})
    return out


def _components(payload: dict) -> list[dict]:
    out = []
    for part in [p.strip() for p in str(payload.get("score_breakdown") or "").split(" | ") if p.strip()]:
        m = re.match(r"([a-z_]+)=([^ ]+) \((.*)\)$", part)
        if m:
            out.append({"component": m.group(1), "value": m.group(2), "evidence": m.group(3)})
    return out


def load_context(svc, user_id: int, key: str) -> ReportContext:
    from ..data.validation import clean_and_validate, usable
    from ..analysis.context import analyze_tf
    from ..screener.scanner import data_snapshot_hash
    rec = svc.store.get_signal(key, user_id)
    if rec is None:
        raise KeyError("signal not found")
    p = rec["payload"] or {}
    cfg = svc.cfg(user_id)
    prov = svc.provider(cfg)
    t = rep = None
    match = None
    try:
        raw = prov.fetch_many([rec["ticker"]], rec["timeframe"]).get(rec["ticker"])
        clean, rep = clean_and_validate(raw, rec["ticker"], rec["timeframe"], cfg, data_kind=prov.data_kind)
        if usable(rep):
            # truncate to the signal's data bar so the chart shows what the signal saw (no later bars)
            cut = clean[[str(x)[:16] <= str(rec["data_time"])[:16] for x in clean.index]] if rec.get("data_time") else clean
            t = analyze_tf(cut if len(cut) >= 30 else clean, rec["timeframe"], cfg)
            if p.get("data_snapshot"):
                match = data_snapshot_hash(t.df) == p["data_snapshot"]
    except Exception:  # noqa: BLE001 - report still generated without a chart
        t = None
    events = [e for e in svc.store.signal_events(key, user_id) if e["from_state"] is not None]   # creation is the record itself
    return ReportContext(rec, p, t, rep, match, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), events)


def _r2(x):
    return None if x is None else round(float(x), 2)


def summary(ctx: ReportContext) -> dict:
    r, p = ctx.record, ctx.payload
    return {"signal_ref": r["key"], "schema": p.get("schema"), "symbol": p.get("symbol"), "ticker": r["ticker"], "exchange": "NSE",
            "timeframe": r["timeframe"], "strategy": r["strategy"], "strategy_version": r["strategy_version"], "setup": r["setup"],
            "direction": r["direction"], "status": r["status"], "original_status": r["status"],
            "first_recorded_utc": str(r.get("first_seen"))[:19], "lifecycle_state": r.get("state"), "label": r["label"],
            "score": r["score"], "score_coverage": p.get("score_coverage"), "signal_bar": r["signal_time"], "data_bar": r["data_time"],
            "data_status": p.get("data_status"), "provider": p.get("provider"), "data_kind": p.get("data_kind"),
            "data_snapshot": p.get("data_snapshot"), "evaluated_at": p.get("evaluated_at"), "entry_zone": p.get("entry_zone"),
            "entry_condition": p.get("entry_condition"), "stop": _r2(r.get("stop")), "stop_method": p.get("stop_method"),
            "target": _r2(r.get("target")), "target_method": p.get("target_method"), "rr": _r2(r.get("rr")),
            "invalidation": _r2(p.get("invalidation")), "expiry": p.get("expiry"), "warnings": p.get("warnings"),
            "chart_data_matches_signal_snapshot": ctx.snapshot_match, "report_generated": ctx.generated_utc}


def to_json(ctx: ReportContext) -> bytes:
    return json.dumps({"summary": summary(ctx), "conditions": _conditions(ctx.payload), "score_components": _components(ctx.payload),
                       "lifecycle_events": _events(ctx), "disclaimer": DISCLAIMER}, indent=2, default=str).encode()


def _events(ctx: ReportContext) -> list[dict]:
    """Lifecycle events that happened after the ORIGINAL (immutable) signal record was created."""
    return [{"at_utc": str(e["ts"])[:19], "from": e["from_state"], "to": e["to_state"], "reason": e["reason"]} for e in ctx.events]


def to_csv(ctx: ReportContext) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "name", "value", "detail"])
    for k, v in summary(ctx).items():
        w.writerow(["summary", k, v, ""])
    for c in _conditions(ctx.payload):
        w.writerow(["condition", c["condition"], c["result"] + (" (mandatory)" if c["mandatory"] else ""), c["detail"]])
    for c in _components(ctx.payload):
        w.writerow(["score_component", c["component"], c["value"], c["evidence"]])
    for e in _events(ctx):
        w.writerow(["lifecycle_event", f"{e['from']} -> {e['to']}", e["at_utc"], e["reason"]])
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx(ctx: ReportContext) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(list(summary(ctx).items()), columns=["field", "value"]).astype(str).to_excel(xw, sheet_name="Signal", index=False)
        pd.DataFrame(_conditions(ctx.payload) or [{"condition": "-"}]).to_excel(xw, sheet_name="Conditions", index=False)
        pd.DataFrame(_components(ctx.payload) or [{"component": "-"}]).to_excel(xw, sheet_name="Score", index=False)
        if ctx.analysis is not None:
            bars = ctx.analysis.df.iloc[-250:][["Open", "High", "Low", "Close", "Volume", "ema20", "ema50", "rsi14", "atr14"]].copy()
            bars.index = [str(i) for i in bars.index]
            bars.to_excel(xw, sheet_name="Bars")
        pd.DataFrame({"note": [DISCLAIMER]}).to_excel(xw, sheet_name="Disclaimer", index=False)
    return buf.getvalue()


def to_png(ctx: ReportContext) -> tuple[bytes, list[dict]]:
    if ctx.analysis is None:
        raise RuntimeError("chart unavailable: no usable market data for this symbol/timeframe right now")
    s = summary(ctx)
    title = f"{s['symbol']} · {s['timeframe']} · {s['setup']} · {s['direction']} · {s['label']} ({s['score']})"
    r = ctx.record                 # exact stored values (summary values are rounded for display)
    return render_chart(ctx.analysis, 120, {"stop": r.get("stop"), "target": r.get("target"), "signal_time": r["signal_time"]}, title)


def to_pdf(ctx: ReportContext, mode: str = "standard", ai_text: str | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    st = getSampleStyleSheet()
    s = summary(ctx)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"AMW signal report {s['symbol']}", author="AMW AI Custom Stock Screener")
    P = lambda txt, sty="BodyText": Paragraph(escape(str(txt)), st[sty])  # noqa: E731

    def table(rows, widths=None):
        tb = Table([[P(c) for c in r] for r in rows], colWidths=widths, repeatRows=1)
        tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eceff1")), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                                ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return tb

    el = [P(f"{s['symbol']} — {s['setup']}", "Title"),
          P(f"{s['direction']} · {s['timeframe']} · {s['label']} (score {s['score']}, coverage {s['score_coverage']}%) · "
            f"original status {s['original_status']} (recorded {s['first_recorded_utc']} UTC) / current lifecycle {s['lifecycle_state']}"),
          Spacer(1, 4),
          P("Executive summary", "Heading2"),
          P(f"Signal bar {s['signal_bar']} (data to {s['data_bar']}, {s['data_status']}). Entry: {s['entry_condition'] or '-'} "
            f"(zone {s['entry_zone'] or '-'}). Stop {s['stop']} ({s['stop_method']}), target {s['target']} ({s['target_method']}), "
            f"R:R {round(s['rr'], 2) if s['rr'] else 'NA'}. Expiry: {s['expiry'] or '-'}.")]
    el += [P("Data source and timestamps", "Heading2"),
           table([["Field", "Value"], ["Provider", f"{s['provider']} ({s['data_kind']})"], ["Signal evaluated", s["evaluated_at"]],
                  ["Data snapshot", s["data_snapshot"]],
                  ["Chart data vs snapshot", SNAP_TEXT[s["chart_data_matches_signal_snapshot"]]],
                  ["Report generated", s["report_generated"]]], [45 * mm, 135 * mm])]
    if ctx.analysis is not None:
        png, _ = to_png(ctx)
        el += [P("Annotated chart", "Heading2"), Image(io.BytesIO(png), width=182 * mm, height=103 * mm)]
    else:
        el += [P("Chart unavailable: no usable market data at report time.", "Italic")]
    conds = _conditions(ctx.payload)
    el += [P("Condition-by-condition results", "Heading2"),
           table([["Condition", "Result", "Mandatory", "Detail"]] + [[c["condition"], c["result"], "yes" if c["mandatory"] else "",
                                                                       c["detail"]] for c in conds] if conds else [["-", "-", "-", "-"]],
                 [45 * mm, 22 * mm, 24 * mm, 89 * mm])]
    if mode in ("standard", "detailed"):
        comps = _components(ctx.payload)
        el += [P("Score components", "Heading2"),
               table([["Component", "Value", "Evidence"]] + [[c["component"], c["value"], c["evidence"]] for c in comps] or [["-"] * 3],
                     [40 * mm, 18 * mm, 122 * mm])]
        t = ctx.analysis
        if t is not None:
            el += [P("Market structure and levels", "Heading2"),
                   P("Recent confirmed swings: " + ", ".join(f"{x.label} {x.price:.2f} ({t.time(x.i)})" for x in t.swings[-6:]) or "none"),
                   P("Structure events: " + ", ".join(f"{e.type} {e.direction} {e.level:.2f} ({t.time(e.i)})" for e in t.events[-4:]) or "none"),
                   P("Support/resistance: " + ", ".join(f"{x['role']} {x['level']:.2f} ({x['touches']}x)" for x in t.sr[:6]) or "none")]
    if mode == "detailed" and ai_text:
        el += [PageBreak(), P("AI explanation (interpretation, not verified fact)", "Heading2"), P(ai_text)]
    ev = _events(ctx)
    el += [P("Lifecycle events after the original signal", "Heading2"),
           P("The original signal record above is immutable; later changes are listed here as separate events.", "Italic"),
           table([["When (UTC)", "From", "To", "Reason"]] + ([[e["at_utc"], e["from"], e["to"], e["reason"]] for e in ev]
                                                            or [["-", "-", "-", "no lifecycle change yet"]]),
                 [32 * mm, 30 * mm, 30 * mm, 88 * mm])]
    el += [P("Limitations", "Heading2"), P(str(s["warnings"] or "None recorded.")),
           P("Methodology and disclaimer", "Heading2"),
           P("Signals are computed on completed candles only by deterministic rules (see STRATEGY_RULES). " + DISCLAIMER)]
    doc.build(el, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.drawString(14 * 2.835, 8 * 2.835, f"AMW AI Custom Stock Screener · page {doc.page} · analysis only")
    canvas.restoreState()


def safe_name(*parts: str, ext: str) -> str:
    base = "_".join(re.sub(r"[^A-Za-z0-9.\-]+", "-", str(p))[:40] for p in parts if p)
    return f"{base}.{ext}"


def generate(svc, user_id: int, key: str, fmt: str, mode: str = "standard", ai_text: str | None = None) -> tuple[str, bytes, dict]:
    """Returns (filename, content, metadata) and records the generation. Raises KeyError for unknown/foreign signals."""
    if fmt not in FORMATS or mode not in MODES:
        raise ValueError(f"format must be one of {FORMATS} and mode one of {MODES}")
    from ..saas.entitlements import check
    check(svc.store, user_id, "reports_per_day", svc.store.reports_today(user_id) + 1)
    ctx = load_context(svc, user_id, key)
    rid = svc.store.report_start(user_id, key, fmt, mode)
    try:
        if fmt == "json":
            data = to_json(ctx)
        elif fmt == "csv":
            data = to_csv(ctx)
        elif fmt == "xlsx":
            data = to_xlsx(ctx)
        elif fmt == "png":
            data, _ = to_png(ctx)
        else:
            data = to_pdf(ctx, mode, ai_text)
    except Exception as e:  # noqa: BLE001
        svc.store.report_finish(user_id, rid, "FAILED", None, 0, ctx.snapshot_match, f"{type(e).__name__}: {e}"[:300])
        raise
    # report id in the name: each generation is its own file (A-009 - identical names overwrote earlier reports)
    name = safe_name("AMW", ctx.payload.get("symbol") or ctx.record["ticker"], ctx.record["timeframe"], key[:8], mode,
                     f"r{rid}", ext=fmt)
    folder = reports_dir(svc) / str(int(user_id))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    svc.store.report_finish(user_id, rid, "COMPLETED", name, len(data), ctx.snapshot_match, None)
    try:
        enforce_quota(svc, user_id, keep_id=rid)
    except Exception:  # noqa: BLE001 - the report itself succeeded
        pass
    return name, data, {"report_id": rid, "snapshot_match": ctx.snapshot_match, "bytes": len(data)}


def reports_dir(svc) -> Path:
    """Where generated report files live: AMW_REPORTS_DIR, else <app root>/data/reports (one sub-folder per user).
    In Docker this must be a persistent volume shared by the app and api containers."""
    return Path(os.environ.get("AMW_REPORTS_DIR") or Path(getattr(svc, "root", ROOT)) / "data" / "reports")


def read_report(svc, user_id: int, report_id: int) -> tuple[str, bytes]:
    r = svc.store.report_get(user_id, report_id)
    if not r or r["status"] != "COMPLETED":
        raise KeyError("report not found")
    base = (reports_dir(svc) / str(int(user_id))).resolve()
    path = (base / r["filename"]).resolve()
    if base not in path.parents:
        raise KeyError("report not found")
    return r["filename"], path.read_bytes()


RECENT_PROTECTION_MIN = 30      # reports younger than this may still be in delivery (scheduler/Telegram): never auto-deleted


def _protected(r: dict, now) -> str | None:
    from datetime import timedelta
    if r.get("legal_hold"):
        return "legal hold"
    if r["status"] != "COMPLETED":
        return f"status {r['status']}"
    if r.get("created") and r["created"] > now - timedelta(minutes=RECENT_PROTECTION_MIN):
        return "recent (may be in delivery)"
    return None


def _delete_file(svc, user_id: int, r: dict, reason: str) -> int:
    """Remove the file, then mark the row DELETED with the reason. Returns bytes freed, or -1 when the file could
    not be removed (row left COMPLETED with the error so the next run retries)."""
    n = 0
    try:
        if r.get("filename"):
            base = (reports_dir(svc) / str(int(user_id))).resolve()
            path = (base / r["filename"]).resolve()
            if base in path.parents and path.exists():
                n = path.stat().st_size
                path.unlink()
    except OSError as e:
        import logging
        logging.getLogger(__name__).warning("report cleanup failed for report %s: %s", r["id"], type(e).__name__)
        return -1
    svc.store.report_mark_deleted(user_id, r["id"], reason)
    return n


def enforce_quota(svc, user_id: int, keep_id: int | None = None) -> int:
    """Plan storage quota (report_storage_mb): delete the OLDEST report files until the user is under the quota."""
    from ..saas.entitlements import plan_limits
    plan = (svc.store.get_user(user_id) or {}).get("plan") or "free"
    mb = plan_limits(plan, svc.base_cfg).get("report_storage_mb")
    if mb is None:
        return 0
    from ..storage.db import utcnow
    now = utcnow()
    rows = svc.store.reports_completed(user_id)
    total, freed = sum(r["bytes"] or 0 for r in rows), 0
    for r in rows:                                   # oldest first
        if total <= mb * 1024 * 1024:
            break
        if r["id"] == keep_id or _protected(r, now):
            continue
        b = _delete_file(svc, user_id, r, f"deleted: storage quota {mb} MB of plan '{plan}'")
        if b >= 0:
            total -= r["bytes"] or 0
            freed += b
    return freed


def apply_retention(svc, now) -> dict:
    """Delete report files older than the plan's report_retention_days (rows stay, marked DELETED with the reason)."""
    from datetime import timedelta
    from ..saas.entitlements import plan_limits
    out = {"deleted": 0, "bytes": 0, "protected": 0, "failed": 0}
    by_user: dict[int, list[dict]] = {}
    for r in svc.store.reports_older_than(None, now):
        if r["status"] == "COMPLETED":
            by_user.setdefault(r["user_id"], []).append(r)
    for uid, rows in by_user.items():                 # per user: each user's own plan, own files only
        plan = (svc.store.get_user(uid) or {}).get("plan") or "free"
        days = plan_limits(plan, svc.base_cfg).get("report_retention_days")
        if days is None:
            continue
        cut = now - timedelta(days=int(days))
        for r in rows:
            if r["created"] >= cut:
                continue
            if _protected(r, now):
                out["protected"] += 1
                continue
            b = _delete_file(svc, uid, r, f"deleted: retention {days} days of plan '{plan}'")
            if b < 0:
                out["failed"] += 1
            else:
                out["bytes"] += b
                out["deleted"] += 1
    if any(out[k] for k in ("deleted", "failed")):
        svc.store.audit("report_retention", json.dumps(out))
    return out

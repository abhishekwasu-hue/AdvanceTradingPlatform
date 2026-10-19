"""Research reports beyond the single-signal report: backtest report, forward-test report, daily scanner report.
All numbers come from stored records / the backtest result object - nothing is recomputed or estimated here.
PDF via reportlab, tables as CSV. Every report carries the methodology note and the analysis-only disclaimer."""
from __future__ import annotations

import io

import pandas as pd

DISCLAIMER = ("Analysis and simulation only - not investment advice. No orders are placed. Past or simulated results do "
              "not guarantee future results. Scores are rule-based confluence measures, not probabilities.")


def _pdf(title: str, blocks: list[tuple[str, object]]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    ss = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=title, author="AMW AI Custom Stock Screener")
    el = [Paragraph(title, ss["Title"])]
    for head, body in blocks:
        el.append(Paragraph(head, ss["Heading3"]))
        if isinstance(body, pd.DataFrame):
            if body.empty:
                el.append(Paragraph("No rows.", ss["BodyText"]))
            else:
                b = body.head(60).astype(str)
                t = Table([list(b.columns)] + b.values.tolist(), repeatRows=1)
                t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                                       ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
                el.append(t)
                if len(body) > 60:
                    el.append(Paragraph(f"... {len(body) - 60} more rows in the CSV export.", ss["BodyText"]))
        else:
            el.append(Paragraph(str(body), ss["BodyText"]))
        el.append(Spacer(1, 4 * mm))
    el.append(Paragraph(DISCLAIMER, ss["Italic"]))
    doc.build(el)
    return buf.getvalue()


def backtest_report(res: dict, title: str) -> tuple[bytes, bytes]:
    """(pdf, csv of trades) from a run_backtest / lab_backtest result."""
    trades = res.get("trades") if isinstance(res.get("trades"), pd.DataFrame) else pd.DataFrame()
    m = pd.DataFrame([{"segment": k, **{kk: vv for kk, vv in (res.get(k) or {}).items() if not isinstance(vv, (dict, list))}}
                      for k in ("all", "in_sample", "out_of_sample")])
    wf = res.get("walk_forward") if isinstance(res.get("walk_forward"), pd.DataFrame) else pd.DataFrame()
    blocks = [("Configuration", f"strategy {res.get('strategy')} version {res.get('version')} · timeframe {res.get('timeframe')} · "
                                f"{res.get('bars')} bars · split {res.get('split_time')} · gap-skipped entries "
                                f"{res.get('skipped_gap_entries', 0)}"),
              ("Metrics (after costs)", m), ("Walk-forward folds", wf), ("Benchmark", str(res.get("benchmark"))),
              ("Trades", trades),
              ("Methodology", "Signals from bars <= j only; entry at the next bar's open; stop assumed first when stop and "
                              "target touch the same bar; gaps fill at the open; configured costs applied per side. In-sample "
                              "results are not independent validation; fewer than 30 trades = INSUFFICIENT EVIDENCE.")]
    return _pdf(title, blocks), trades.to_csv(index=False).encode("utf-8-sig")


def forward_report(store, user_id: int) -> tuple[bytes, bytes]:
    from ..services.forward import stats
    st = pd.DataFrame(stats(store, user_id))
    items = pd.DataFrame(store.forward_list(user_id, limit=100000))
    if not len(items):
        items = pd.DataFrame(columns=["ticker", "strategy", "strategy_version", "status", "r_multiple"])
    if len(items):
        items = items.drop(columns=["user_id"], errors="ignore")
    blocks = [("Per strategy version", st), ("Forward tests", items),
              ("Methodology", "Paper simulation of every qualified signal: entry at the open of the first completed bar after "
                              "the signal bar; exit at stop/target (stop assumed if both in one bar; gaps at the open) or "
                              "after a fixed number of bars. No costs or slippage.")]
    return _pdf("AMW forward-test report", blocks), items.to_csv(index=False).encode("utf-8-sig")


def daily_report(store, user_id: int, day: str) -> tuple[bytes, bytes]:
    hist = [{k: v for k, v in h.items() if k != "payload"} for h in store.signal_history(user_id, limit=2000)
            if str(h["first_seen"])[:10] == day]
    cols = ["ticker", "strategy", "strategy_version", "setup", "timeframe", "direction", "signal_time", "status",
            "label", "score", "stop", "target", "state", "key"]
    df = pd.DataFrame(hist, columns=None if hist else cols)
    df = df[[c for c in cols if c in df.columns]]          # header is always written, even with no rows
    q = int((df.status == "qualified").sum()) if len(df) else 0
    blocks = [("Summary", f"{day}: {len(df)} new stored signals, {q} qualified. Data source and snapshot per signal are in "
                          "each signal's canonical record (JSON report)."), ("Signals", df)]
    return _pdf(f"AMW daily scanner report {day}", blocks), df.to_csv(index=False).encode("utf-8-sig")

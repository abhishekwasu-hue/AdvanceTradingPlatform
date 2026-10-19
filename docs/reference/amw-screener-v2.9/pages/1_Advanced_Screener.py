import numpy as np
import pandas as pd
import streamlit as st

from src.config import STRATEGIES
from src.screener.export import export_name, to_csv_bytes
from src.screener.scanner import apply_filters, signals_frame
from ui_common import page

svc = page("Advanced Screener", "🔎")
uid = svc.local_user
cfg = svc.cfg()

# ---------------- universe + scan
with st.container(border=True):
    st.markdown("#### Universe and scan")
    c = st.columns([2, 2, 1, 1])
    wls = [f"Watchlist: {w['name']}" for w in svc.store.watchlists(uid)]
    uni_name = c[0].selectbox("Universe", list(cfg["universe"]["sources"]) + ["Custom"] + wls)
    custom = c[1].text_input("Custom symbols", "RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK", disabled=uni_name != "Custom")
    limit = c[2].number_input("First N (0 = all)", 0, 3000, 10, 10)
    refresh = c[3].button("🔄 List refresh")
    uni = svc.universe(uni_name, custom, force=refresh)
    if uni.is_fallback:
        st.error(f"⚠️ {uni.source} — {uni.count} symbols. Official list unavailable: place the NSE CSV in `data/` (see README).")
    else:
        st.caption(f"Source: {uni.source} · {uni.count} symbols · {uni.retrieved_at}")
    for e in uni.errors:
        st.caption(f"• {e}")
    ds = svc.data_source_status(uid)
    if ds["fallback"]:
        st.warning(f"⚠️ Data source: FYERS selected, but {ds['reason']}. Scans now use **{ds['active']}**.")
    else:
        st.caption(f"Data source: {ds['active']}")
    on = [k for k, v in cfg["strategies"].items() if v["enabled"]]
    strats = st.multiselect("Strategies", STRATEGIES, default=on,
                            format_func=lambda s: s + ("" if s in on else " (disabled in Workbench)"))
    b = st.columns(3)
    job_id = st.session_state.get("job_id")
    job = svc.jobs.get(job_id) if job_id else None
    running = job is not None and job.status in ("queued", "running")
    if b[0].button("▶️ Start scan", type="primary", disabled=running or not strats or uni.count == 0):
        try:
            j = svc.start_scan(uni.symbols, uni.name, [s for s in strats if s in on], int(limit) or None)
            st.session_state.job_id = j.id
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    if b[1].button("⏹ Cancel", disabled=not running):
        svc.jobs.cancel(job_id)
    can_resume = job is not None and job.status in ("cancelled",) and getattr(job, "partial", None) is not None
    if b[2].button("⏯ Resume", disabled=not can_resume):
        j = svc.start_scan(uni.symbols, uni.name, [s for s in strats if s in on], int(limit) or None, resume=job.partial)
        st.session_state.job_id = j.id
        st.rerun()

    @st.fragment(run_every=2 if running else None)
    def progress_box():
        j = svc.jobs.get(st.session_state.get("job_id", ""))
        if j is None:
            return
        st.progress(min(1.0, j.progress), text=f"{j.status} · {j.message}")
        if j.status == "failed":
            st.error(j.error)
        if j.status in ("done", "cancelled") and st.session_state.get("shown") != j.id:
            st.session_state.shown = j.id
            st.rerun()
    progress_box()

res = svc.last_scan
if res is None:
    st.stop()

# ---------------- filters + presets
st.markdown("#### Filters")
presets = {p["name"]: p["payload"] for p in svc.store.presets(uid, "screener")}
pc = st.columns([2, 2, 1])
chosen = pc[0].selectbox("Saved preset", ["—"] + list(presets))
P = presets.get(chosen, {})
f = {}
c = st.columns(4)
f["strategies"] = c[0].multiselect("Strategy", STRATEGIES, default=P.get("strategies", []))
f["timeframes"] = c[1].multiselect("Timeframe", res.timeframes, default=[t for t in P.get("timeframes", []) if t in res.timeframes])
d = c[2].selectbox("Direction", ["both", "bullish", "bearish"], index=["both", "bullish", "bearish"].index(P.get("direction", "both")))
f["direction"] = None if d == "both" else d
f["status"] = c[3].multiselect("Status", ["qualified", "candidate", "rejected"], default=P.get("status", ["qualified", "candidate"]))
c = st.columns(4)
f["min_score"] = c[0].slider("Min score", 0, 100, int(P.get("min_score", 0)))
f["min_rr"] = c[1].number_input("Min R:R", 0.0, 10.0, float(P.get("min_rr", 0.0)), 0.1)
f["search"] = c[2].text_input("Search symbol / company", P.get("search", ""))
f["data_ok_only"] = c[3].checkbox("OK data only", P.get("data_ok_only", False))
c = st.columns(4)
pmin, pmax = c[0].number_input("Min price", 0.0, 1e6, float(P.get("pmin", 0.0))), c[1].number_input("Max price", 0.0, 1e6, float(P.get("pmax", 1e6)))
min_tv = c[2].number_input("Min avg traded value (₹ Cr)", 0.0, 1e5, float(P.get("min_tv", 0.0)))
rs_only = c[3].checkbox("RS > 0 (stronger than benchmark)", P.get("rs_only", False))
name = pc[1].text_input("Preset name")
if pc[2].button("💾 Preset save") and name:
    svc.store.save_preset(uid, name, "screener", {**f, "direction": d, "pmin": pmin, "pmax": pmax, "min_tv": min_tv, "rs_only": rs_only})
    st.success("saved")

sig = signals_frame(res, include_rejected="rejected" in f["status"])
df = apply_filters(sig, f)
sym = pd.DataFrame(res.symbols)
if not df.empty and not sym.empty:
    df = df.merge(sym[["ticker", "close", "rsi14", "rvol", "atr_pct", "avg_traded_value"]], on="ticker", how="left")
    df = df[df.close.between(pmin, pmax)]
    if min_tv > 0:
        df = df[df.avg_traded_value >= min_tv * 1e7]
    if rs_only:
        df = df[df.score_breakdown.str.contains(r"RS vs benchmark \+", regex=True, na=False)]

st.markdown(f"#### Results: {len(df)} signals")
if df.empty:
    st.info("No signals match the filters. No-trade is a valid outcome.")
else:
    ps = 50
    pages = int(np.ceil(len(df) / ps))
    pg = st.number_input("Page", 1, max(1, pages), 1) if pages > 1 else 1
    cols = ["symbol", "strategy", "setup", "timeframe", "direction", "status", "label", "score", "score_coverage", "rr",
            "entry_zone", "stop", "target", "signal_time", "data_time", "data_status", "close", "rsi14", "rvol", "warnings"]
    st.dataframe(df[[c for c in cols if c in df]].iloc[(pg - 1) * ps: pg * ps], hide_index=True, width="stretch",
                 column_config={"score": st.column_config.ProgressColumn("score", min_value=0, max_value=100, format="%.1f"),
                                "rr": st.column_config.NumberColumn(format="%.2f"), "stop": st.column_config.NumberColumn(format="%.2f"),
                                "target": st.column_config.NumberColumn(format="%.2f"), "close": st.column_config.NumberColumn(format="%.2f"),
                                "rsi14": st.column_config.NumberColumn(format="%.1f"), "rvol": st.column_config.NumberColumn(format="%.2f")})
    st.download_button("⬇️ Filtered results CSV", to_csv_bytes(df), export_name("signals"), "text/csv")
    pick = st.selectbox("Open in stock detail", sorted(df.ticker.unique()))
    if st.button("➡️ Open in Price Action Workbench"):
        st.session_state.detail_ticker = pick
        st.switch_page("pages/3_Price_Action_Workbench.py")

with st.expander("Multi-timeframe matrix"):
    m = pd.DataFrame(res.matrix)
    st.dataframe(m, hide_index=True, width="stretch")
    st.download_button("⬇️ Matrix CSV", to_csv_bytes(m), export_name("mtf_matrix"), "text/csv")
with st.expander("Data quality"):
    q = pd.DataFrame(res.quality)
    st.dataframe(q, hide_index=True, width="stretch")
    st.download_button("⬇️ Data quality CSV", to_csv_bytes(q), export_name("data_quality"), "text/csv")

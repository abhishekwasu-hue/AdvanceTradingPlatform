import copy

import pandas as pd
import streamlit as st

from src.config import TIMEFRAME_INFO
from ui_common import page

svc = page("Settings & System Health", "⚙️")
uid = svc.local_user
cfg = svc.cfg()
ov = copy.deepcopy(svc.store.get_pref(uid, "settings_override", {}) or {})

t1, t2, t3, t4 = st.tabs(["Risk & costs", "Data & market session", "System health", "Audit log"])
with t1:
    with st.form("risk"):
        r = cfg["risk"]
        c = st.columns(3)
        eq = c[0].number_input("Account equity (₹) — 0 = not set", 0.0, 1e11, float(r["account_equity"] or 0))
        rpt = c[1].number_input("Risk per trade %", 0.05, 10.0, float(r["risk_per_trade_pct"]), 0.05)
        mdl = c[2].number_input("Max daily loss %", 0.1, 50.0, float(r["max_daily_loss_pct"]), 0.1)
        c = st.columns(3)
        mor = c[0].number_input("Max open risk %", 0.1, 100.0, float(r["max_open_risk_pct"]), 0.1)
        msp = c[1].number_input("Max positions per sector", 1, 50, int(r["max_sector_positions"]))
        mtv = c[2].number_input("Min avg traded value (₹)", 0.0, 1e12, float(r["min_avg_traded_value"]))
        st.markdown("**Costs (bps per side)**")
        c = st.columns(3)
        k = cfg["costs"]
        b1 = c[0].number_input("Brokerage", 0.0, 100.0, float(k["brokerage_bps"]))
        b2 = c[1].number_input("Exchange + taxes", 0.0, 100.0, float(k["exchange_tax_bps"]))
        b3 = c[2].number_input("Slippage", 0.0, 100.0, float(k["slippage_bps"]))
        st.markdown("**Score labels**")
        c = st.columns(3)
        la = c[0].number_input("A1 ≥", 0, 100, int(cfg["scoring"]["labels"]["a1"]))
        ls = c[1].number_input("Strong ≥", 0, 100, int(cfg["scoring"]["labels"]["strong"]))
        lw = c[2].number_input("Watchlist ≥", 0, 100, int(cfg["scoring"]["labels"]["watch"]))
        if st.form_submit_button("💾 Save"):
            ov["risk"] = {"account_equity": eq or None, "risk_per_trade_pct": rpt, "max_daily_loss_pct": mdl, "max_open_risk_pct": mor,
                          "max_sector_positions": int(msp), "min_avg_traded_value": mtv}
            ov["costs"] = {"brokerage_bps": b1, "exchange_tax_bps": b2, "slippage_bps": b3}
            ov["scoring"] = {"labels": {"a1": la, "strong": ls, "watch": lw}}
            try:
                svc.save_override(ov)
                st.success("saved")
            except ValueError as e:
                st.error(f"Invalid: {e}")
    if st.button("↩️ Restore defaults (remove risk/costs/labels/data overrides)"):
        svc.save_override({})
        st.rerun()
with t2:
    st.markdown(f"**Provider:** {svc.provider(cfg).name} · **data kind:** {svc.provider(cfg).data_kind} — not real-time.")
    st.dataframe(pd.DataFrame(TIMEFRAME_INFO).T, width="stretch")
    with st.form("data"):
        d, m = cfg["data"], cfg["market"]
        c = st.columns(4)
        bs = c[0].number_input("Batch size", 1, 100, int(d["batch_size"]))
        pa = c[1].number_input("Pause between batches (s)", 0.0, 60.0, float(d["pause_between_batches"]))
        mr = c[2].number_input("Max retries", 0, 5, int(d["max_retries"]))
        bm = c[3].text_input("Benchmark", cfg["benchmark"])
        c = st.columns(3)
        tz = c[0].text_input("Timezone", m["timezone"])
        so = c[1].text_input("Session open", m["session_open"])
        scl = c[2].text_input("Session close", m["session_close"])
        hol = st.text_area("Exchange holidays (YYYY-MM-DD, one per line) — fill from the official NSE list", "\n".join(m.get("holidays", [])))
        if st.form_submit_button("💾 Save"):
            ov["data"] = {"batch_size": int(bs), "pause_between_batches": pa, "max_retries": int(mr)}
            ov["benchmark"] = bm.strip()
            ov["market"] = {"timezone": tz, "session_open": so, "session_close": scl,
                            "holidays": [h.strip() for h in hol.splitlines() if h.strip()]}
            try:
                svc.save_override(ov)
                st.success("saved")
            except ValueError as e:
                st.error(str(e))
with t3:
    h = svc.health()
    k = st.columns(3)
    k[0].metric("Database", f"{h['database']['backend']} · {'OK' if h['database'].get('ok') else 'FAIL'}")
    k[1].metric("DB ping", f"{h['database'].get('ping_ms', 'NA')} ms")
    k[2].metric("Schema version", h["database"].get("schema_version", "NA"))
    st.markdown("**Provider / cache**")
    st.json(h["provider"])
    if st.button("🧹 Clear OHLCV cache"):
        n = svc.provider(cfg).cache.clear() if getattr(svc.provider(cfg), "cache", None) else 0
        st.success(f"{n} files removed")
    st.markdown("**Background jobs**")
    st.dataframe(pd.DataFrame(svc.jobs.list()), hide_index=True, width="stretch")
    st.markdown("**Scan runs**")
    st.dataframe(pd.DataFrame(svc.store.scans()), hide_index=True, width="stretch")
    st.markdown("**Recent errors (amw_screener.log)**")
    from src.config import ROOT
    lp = ROOT / "amw_screener.log"
    lines = [x for x in lp.read_text(encoding="utf-8", errors="ignore").splitlines() if " ERROR " in x or " WARNING " in x][-50:] if lp.exists() else []
    st.code("\n".join(lines) or "no errors")
with t4:
    st.dataframe(pd.DataFrame(svc.store.audit_rows()), hide_index=True, width="stretch")
    st.caption("Desktop mode has a single local admin user. Multi-user role-based access is provided by the REST API (token auth).")

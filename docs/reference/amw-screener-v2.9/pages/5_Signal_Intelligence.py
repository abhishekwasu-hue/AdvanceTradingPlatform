import pandas as pd
import streamlit as st

from src.screener.export import export_name, to_csv_bytes
from src.screener.scanner import signals_frame
from ui_common import page

svc = page("Signal Intelligence", "📡")
res = svc.last_scan
t1, t2 = st.tabs(["Current scan (ranked)", "Stored signals & lifecycle"])
with t1:
    if res is None:
        st.info("Ranked signals appear here after a scan.")
    else:
        df = signals_frame(res, include_rejected=st.checkbox("Show rejected setups too (with reasons)"))
        if df.empty:
            st.info("No qualifying setup — no-trade is a valid outcome.")
        else:
            for _, r in df.head(30).iterrows():
                with st.container(border=True):
                    c = st.columns([3, 1, 1, 1, 1])
                    c[0].markdown(f"**{r.symbol}** · {r.setup} · {r.timeframe} · **{r.direction}**  \n`{r.strategy} v{r.strategy_version}` · signal bar {r.signal_time} · data {r.data_time} ({r.data_status})")
                    c[1].metric("Score", f"{r.score:.1f}", r.label, delta_color="off")
                    c[2].metric("Coverage", f"{r.score_coverage:.0f}%")
                    c[3].metric("R:R", "NA" if pd.isna(r.rr) else f"{r.rr:.2f}")
                    c[4].metric("Status", r.status)
                    st.markdown(f"Entry zone `{r.entry_zone}` · stop `{r.stop if pd.isna(r.stop) else round(r.stop, 2)}` ({r.stop_method}) · "
                                f"target `{r.target if pd.isna(r.target) else round(r.target, 2)}` ({r.target_method}) · expiry: {r.expiry}")
                    with st.expander("Reasons: passed / failed / score breakdown"):
                        st.markdown("**✅ Passed:** " + (r.conditions_passed or "-"))
                        st.markdown("**❌ Failed** ([M] = mandatory): " + (r.conditions_failed or "-"))
                        st.markdown("**➖ Not evaluable:** " + (r.conditions_na or "-"))
                        st.markdown("**Score breakdown:** " + r.score_breakdown)
                        if r.warnings:
                            st.warning(r.warnings)
            st.download_button("⬇️ Signals CSV", to_csv_bytes(df), export_name("signal_intelligence"), "text/csv")
with t2:
    h = svc.store.signal_history(svc.local_user, limit=500)
    if not h:
        st.info("No stored signals yet. (Qualified/candidate signals are de-duplicated and stored during scans.)")
    else:
        hd = pd.DataFrame(h).drop(columns=["payload"])
        st.dataframe(hd, hide_index=True, width="stretch")
        st.caption("States: candidate → awaiting_confirmation → qualified → active → completed / invalidated / expired; rejected; insufficient_data.")
        key = st.selectbox("View lifecycle", hd.key)
        st.dataframe(pd.DataFrame(svc.store.signal_events(key, svc.local_user)), hide_index=True, width="stretch")
        rec = svc.store.get_signal(key, svc.local_user)
        b = st.columns(2)
        if rec and b[0].button("🕯️ Open on chart", help="Price Action Workbench at this signal's ticker/timeframe with its stored levels"):
            st.session_state.update(detail_ticker=rec["ticker"], detail_tf=rec["timeframe"], focus_signal=key)
            st.switch_page("pages/3_Price_Action_Workbench.py")
        if rec and b[1].button("📄 Create report"):
            st.session_state.report_key = key
            st.switch_page("pages/12_Report_Center.py")

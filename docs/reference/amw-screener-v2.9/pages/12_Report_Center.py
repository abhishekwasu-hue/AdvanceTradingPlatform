import pandas as pd
import streamlit as st

from src.reports import builder
from src.saas.entitlements import EntitlementError, plan_limits, usage
from ui_common import page
from src.reports import research as _rr

svc = page("Report Center", "📄")
uid = svc.local_user
st.caption("Reports are built from the stored signal record. The chart is recomputed from available data up to the signal bar; "
           "the report states whether that data still matches the signal's data snapshot.")
hist = svc.store.signal_history(uid, limit=300)
if not hist:
    st.info("No stored signals yet — run a scan first.")
else:
    df = pd.DataFrame(hist)
    labels = {r["key"]: f"{r['ticker']} · {r['setup']} · {r['timeframe']} · {r['direction']} · {r['signal_time']} · {r['label']}" for r in hist}
    c = st.columns([4, 1, 1])
    keys = list(labels)
    pre = st.session_state.pop("report_key", None)
    key = c[0].selectbox("Signal", keys, index=keys.index(pre) if pre in keys else 0, format_func=labels.get)
    fmt = c[1].selectbox("Format", list(builder.FORMATS), index=4)
    mode = c[2].selectbox("Mode", list(builder.MODES), index=1)
    if st.button("Generate", type="primary"):
        try:
            with st.spinner("Generating…"):
                name, data, meta = builder.generate(svc, uid, key, fmt, mode)
            st.success(f"{name} · {meta['bytes']:,} bytes · data snapshot "
                       + {True: "matches the signal", False: "DIFFERS from the signal (provider data changed)", None: "not verifiable"}[meta["snapshot_match"]])
            if fmt == "png":
                st.image(data)
            st.download_button("⬇️ Download", data, name)
        except EntitlementError as e:
            st.error(str(e))
        except (KeyError, RuntimeError, ValueError) as e:
            st.error(f"Report not generated: {e}")
st.subheader("Generated reports")
reps = svc.store.report_list(uid)
if reps:
    st.dataframe(pd.DataFrame(reps).drop(columns=["user_id"]), hide_index=True, width="stretch")
    rid = st.selectbox("Download an earlier report", [r["id"] for r in reps if r["status"] == "COMPLETED"])
    if rid:
        try:
            name, data = builder.read_report(svc, uid, rid)
            st.download_button(f"⬇️ {name}", data, name, key="old")
        except (KeyError, FileNotFoundError):
            st.warning("File no longer available.")
st.subheader("Research reports")
rc = st.columns(2)
with rc[0]:
    day = st.date_input("Daily scanner report — day", pd.Timestamp.now(tz="Asia/Kolkata").date())
    if st.button("Build daily report"):
        pdf, csv = _rr.daily_report(svc.store, uid, str(day))
        st.download_button("⬇️ Daily report (PDF)", pdf, f"AMW_daily_{day}.pdf", key="dpdf")
        st.download_button("⬇️ Daily signals (CSV)", csv, f"AMW_daily_{day}.csv", key="dcsv")
with rc[1]:
    st.caption("Forward-test report: paper simulation of every qualified signal.")
    if st.button("Build forward-test report"):
        pdf, csv = _rr.forward_report(svc.store, uid)
        st.download_button("⬇️ Forward tests (PDF)", pdf, "AMW_forward_tests.pdf", key="fpdf")
        st.download_button("⬇️ Forward tests (CSV)", csv, "AMW_forward_tests.csv", key="fcsv")
st.caption("Backtest reports are downloaded from the Backtesting page after a run.")
u = svc.store.get_user(uid)
with st.expander("Plan limits and usage (provisional values)"):
    st.caption("Report files are kept for the plan's retention period (provisional defaults: Free 7, Pro 90, Premium/"
               "Enterprise 365 days), then deleted; the record stays with the reason. Database/volume backups follow "
               "their own retention and are not purged immediately. Reports on legal hold are never auto-deleted.")
    st.json({"plan": u["plan"], "limits": plan_limits(u["plan"], svc.cfg()), "usage": usage(svc.store, svc, uid)})

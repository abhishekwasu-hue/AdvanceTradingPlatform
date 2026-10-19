import copy

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from src.backtest.engine import run_backtest, sensitivity
from src.data.validation import clean_and_validate, usable
from ui_common import page

svc = page("Backtesting & Analytics", "🧪")
cfg = copy.deepcopy(svc.cfg())
prov = svc.provider(cfg)
st.warning("Historical simulation — not a guarantee of future results. Survivorship bias (current constituents), unadjusted Yahoo prices "
           "(corporate actions), delayed data. Backtest results are separate from scanner scores.")
c = st.columns(4)
tk = c[0].text_input("Ticker", st.session_state.get("detail_ticker", "RELIANCE.NS")).strip().upper()
tf = c[1].selectbox("Timeframe", ["1D", "1H"])
strat = c[2].selectbox("Strategy", ["breakout", "pullback", "reversal", "trend_continuation", "swing"])
step = c[3].number_input("Evaluation step (bars)", 1, 10, 1)
c = st.columns(5)
start = c[0].text_input("Start (YYYY-MM-DD, optional)", "")
end = c[1].text_input("End (optional)", "")
cfg["costs"]["brokerage_bps"] = c[2].number_input("Brokerage bps/side", 0.0, 100.0, float(cfg["costs"]["brokerage_bps"]))
cfg["costs"]["exchange_tax_bps"] = c[3].number_input("Exchange+tax bps/side", 0.0, 100.0, float(cfg["costs"]["exchange_tax_bps"]))
cfg["costs"]["slippage_bps"] = c[4].number_input("Slippage bps/side", 0.0, 100.0, float(cfg["costs"]["slippage_bps"]))
cfg["backtest"]["max_hold_bars"] = st.slider("Max hold bars", 3, 100, int(cfg["backtest"]["max_hold_bars"]))

if st.button("▶️ Backtest", type="primary"):
    raw = prov.fetch_many([tk, cfg["benchmark"]], tf)
    clean, rep = clean_and_validate(raw.get(tk), tk, tf, cfg)
    bench, _ = clean_and_validate(raw.get(cfg["benchmark"]), "B", tf, cfg)
    if not usable(rep):
        st.error(f"Data not usable: {rep.status}")
        st.stop()
    with st.spinner("Re-analysing each bar using only past data…"):
        r = run_backtest(clean, tf, cfg, strat, bench=bench, start=start or None, end=end or None, step=int(step))
    from src.services.notify_events import backtest_done
    backtest_done(svc.store, svc.local_user, r, strat)
    st.session_state.bt = (r, clean, bench, tf, strat)

if "bt" in st.session_state:
    r, clean, bench, tf_, strat_ = st.session_state.bt
    st.markdown(f"**{strat_} v{r['version']} · {tf_} · {r['bars']} bars · IS/OOS split {r['split_time']} · gap-skipped entries {r['skipped_gap_entries']}**")
    if str(r["all"].get("evidence", "")).startswith("INSUFFICIENT"):
        st.warning(f"{r['all']['evidence']}: these figures describe what happened in this sample only; they are not a validated edge.")
    st.dataframe(pd.DataFrame({k: r[k] for k in ("all", "in_sample", "out_of_sample")}).T, width="stretch")
    st.write("Benchmark:", r["benchmark"])
    from src.reports.research import backtest_report
    _pdf, _csv = backtest_report(r, f"AMW backtest report · {strat_} v{r['version']} · {tf_}")
    dc = st.columns(2)
    dc[0].download_button("⬇️ Backtest report (PDF)", _pdf, f"AMW_backtest_{strat_}_{tf_}.pdf")
    dc[1].download_button("⬇️ Trades (CSV)", _csv, f"AMW_backtest_{strat_}_{tf_}_trades.csv")
    eq = r["equity"]
    if len(eq):
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3])
        fig.add_trace(go.Scatter(x=eq.time, y=eq.equity, name="Equity (normalised)"), 1, 1)
        fig.add_trace(go.Scatter(x=eq.time, y=eq.drawdown_pct, name="Drawdown %", fill="tozeroy", line=dict(color="#d32f2f")), 2, 1)
        fig.update_layout(height=450, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, width="stretch")
        st.markdown("**R-multiple distribution**")
        st.bar_chart(r["trades"].r_multiple.round(1).value_counts().sort_index())
    st.markdown("**Walk-forward folds**")
    st.dataframe(r["walk_forward"], hide_index=True, width="stretch")
    st.markdown("**Trades**")
    st.dataframe(r["trades"], hide_index=True, width="stretch")
    with st.expander("Assumptions"):
        st.code(r["assumptions"])
    st.markdown("#### Parameter sensitivity")
    params = {k: v for k, v in cfg["strategies"][strat_]["params"].items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if params:
        c = st.columns(2)
        pname = c[0].selectbox("Parameter", list(params))
        base = params[pname]
        vals = c[1].text_input("Values (comma)", ", ".join(str(round(base * f, 3)) for f in (0.75, 1.0, 1.25)))
        if st.button("Run sensitivity"):
            vs = [type(base)(float(x)) for x in vals.split(",") if x.strip()]
            with st.spinner("…"):
                st.dataframe(sensitivity(clean, tf_, cfg, strat_, pname, vs, bench=bench), hide_index=True, width="stretch")
            st.caption("Sensitivity checks robustness — do not pick the best value and claim performance on the same data.")

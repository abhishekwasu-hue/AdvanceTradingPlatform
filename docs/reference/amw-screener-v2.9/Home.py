"""AMW AI Custom Stock Screener™ - Page 1: Market Overview.   Run: python -m streamlit run Home.py"""
import pandas as pd
import streamlit as st

from src.analysis.context import analyze_tf
from src.analysis.regime import classify_regime
from src.data.validation import clean_and_validate, usable
from src.screener.scanner import market_overview, signals_frame
from ui_common import badge, num, page

svc = page("Market Overview", "🧭")
cfg = svc.cfg()
bt = cfg["benchmark"]
prov = svc.provider(cfg)

c = st.columns([1, 1, 3])
tf = c[0].selectbox("Benchmark timeframe", ["1D", "1W", "1H"], index=0)
c[1].markdown(f"**Benchmark:** `{bt}`")
with st.spinner("Loading benchmark data…"):
    raw = prov.fetch_many([bt], tf).get(bt)
    clean, rep = clean_and_validate(raw, bt, tf, cfg, data_kind=prov.data_kind)
bench = analyze_tf(clean, tf, cfg) if usable(rep) else None
st.markdown(f"Data status {badge(rep.status)} · last completed bar **{rep.last or 'NA'}** · provider **{prov.name}** ({prov.data_kind})",
            unsafe_allow_html=True)
if bench is None:
    st.error(f"Benchmark data is not usable ({rep.status}): {'; '.join(rep.issues)}. Regime not classified.")
else:
    rg = classify_regime(bench, cfg["analysis"]["compression_ratio"])
    k = st.columns(5)
    k[0].metric("Regime", rg["regime"].replace("_", " "))
    k[1].metric("Volatility", rg["volatility"])
    k[2].metric("Benchmark close", num(bench.close))
    k[3].metric("Trend score", num(bench.trend_score, "{:+.2f}"))
    k[4].metric("ADX", num(bench.last.get("adx"), "{:.1f}"))
    with st.expander("Regime evidence and rules"):
        st.write(rg["evidence"])
        st.code(rg["rules"])

res = svc.last_scan
st.subheader("Breadth, sectors and active signals")
if res is None:
    st.info("Run a scan from the **Advanced Screener** to compute breadth and sector strength (calculated from scanned symbols only).")
else:
    ov = market_overview(res, bench)
    k = st.columns(5)
    k[0].metric("Scanned", f"{res.symbols_done}/{res.total}")
    k[1].metric("% above EMA50", num(ov.get("pct_above_ema50"), "{:.0f}%"))
    k[2].metric("% above EMA200", num(ov.get("pct_above_ema200"), "{:.0f}%"))
    k[3].metric("Advancers", ov.get("advancers", "NA"))
    k[4].metric("Decliners", ov.get("decliners", "NA"))
    st.caption(f"Breadth is based only on the {ov.get('breadth_n', 0)} scanned symbols — scan the full universe for index-level breadth.")
    sig = signals_frame(res)
    cc = st.columns(2)
    with cc[0]:
        st.markdown("**Active signals (strategy × timeframe)**")
        if sig.empty:
            st.write("No qualifying/candidate signals — no-trade is a valid outcome.")
        else:
            st.dataframe(pd.crosstab(sig.strategy, sig.timeframe), width="stretch")
    with cc[1]:
        st.markdown("**Sector strength (avg 20-bar return)**")
        sec = ov.get("sectors")
        if sec is None or len(sec) == 0:
            st.write("Sector data unavailable (custom/fallback universes have no industry field).")
        else:
            st.dataframe(sec, hide_index=True, width="stretch")
    q = pd.DataFrame(res.quality)
    if len(q):
        st.markdown("**Data freshness**")
        st.dataframe(q.groupby(["timeframe", "status"]).size().rename("count").reset_index(), hide_index=True)

st.subheader("Provider health")
st.json(prov.health())
ev = svc.store.alert_events(svc.local_user, 10)
if ev:
    st.subheader("Recent alerts")
    st.dataframe(pd.DataFrame(ev)[["created", "ticker", "channel", "status", "message"]], hide_index=True, width="stretch")

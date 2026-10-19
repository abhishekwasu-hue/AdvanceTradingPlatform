import pandas as pd
import streamlit as st

from src.analysis.regime import classify_regime
from src.config import TIMEFRAME_INFO, TIMEFRAMES
from src.data.validation import clean_and_validate
from src.screener.export import export_name, to_csv_bytes
from src.screener.scanner import analyse_symbol
from src.strategies.base import StrategyContext
from src.strategies.registry import enabled_strategies, evaluate_all, required_timeframes
from src.ui.charts import build_chart
from ui_common import badge, num, page

svc = page("Price Action Workbench", "🕯️")
cfg = svc.cfg()
prov = svc.provider_for(svc.local_user)          # the user's selected data source (default or own FYERS account)

c = st.columns([2, 1, 1])
default = st.session_state.get("detail_ticker", "RELIANCE.NS")
tk = c[0].text_input("Ticker (Yahoo format, e.g. RELIANCE.NS)", default).strip().upper()
if tk and not tk.endswith(".NS") and not tk.startswith("^") and "." not in tk:
    tk += ".NS"
pre_tf = st.session_state.pop("detail_tf", None)
tf = c[1].selectbox("Timeframe", TIMEFRAMES, index=TIMEFRAMES.index(pre_tf) if pre_tf in TIMEFRAMES else 1)
nb = c[2].number_input("Bars", 50, 1500, 200, 50)
st.caption(f"{tf}: Yahoo history {TIMEFRAME_INFO[tf]['max_history']}, {TIMEFRAME_INFO[tf]['frequency']}")
o = st.columns(7)
show = {"ema": o[0].checkbox("EMA", True), "bb": o[1].checkbox("Bollinger"), "vwap": o[2].checkbox("VWAP", tf not in ("1D", "1W")),
        "zones": o[3].checkbox("Zones", True), "sr": o[4].checkbox("S/R", True), "patterns": o[5].checkbox("Patterns", True),
        "refs": o[6].checkbox("Signal levels", True), "swings": True}

strats = enabled_strategies(cfg)
tfs = sorted(set(required_timeframes(strats)) | {tf}, key=TIMEFRAMES.index)
with st.spinner(f"{tk}: {', '.join(tfs)} data and analysis…"):
    raw = {t: prov.fetch_many([tk], t).get(tk) for t in tfs}
    an, reps = analyse_symbol(raw, tk, tfs, cfg, data_kind=prov.data_kind)
    bench = {t: clean_and_validate(prov.fetch_many([cfg["benchmark"]], t).get(cfg["benchmark"]), "B", t, cfg)[0] for t in tfs}
    sigs = [s.to_row() for s in evaluate_all(StrategyContext(tk, tk, an, reps, bench, cfg), strats)]
st.session_state.detail_ticker = tk
rep, a = reps[tf], an[tf]
st.markdown(f"**{tk} · {tf}** — data {badge(rep.status)} · bars {rep.bars} · {rep.first} → **{rep.last}** · {rep.data_kind}"
            + (" · forming candle excluded" if rep.dropped_forming else ""), unsafe_allow_html=True)
if rep.issues:
    st.caption("Issues: " + "; ".join(rep.issues))
if a is None:
    st.error("No usable data for this timeframe — chart/analysis not shown (no data is fabricated).")
    st.stop()

live = [s for s in sigs if s["timeframe"] == tf and s["status"] in ("qualified", "candidate")]
focus = st.session_state.get("focus_signal")
if focus:
    rec = svc.store.get_signal(focus, svc.local_user)
    if rec and rec["ticker"] == tk and rec["timeframe"] == tf:
        live = [rec["payload"]] + [s for s in live if s.get("key") != rec["payload"].get("key")]
        st.info(f"Stored signal {focus[:8]} · {rec['setup']} · {rec['direction']} · signal bar {rec['signal_time']} · "
                f"state {rec['state']} — its recorded stop/target are drawn. Current data may have changed since it was recorded.")
        if st.button("Clear stored signal"):
            st.session_state.pop("focus_signal", None)
            st.rerun()
    else:
        st.session_state.pop("focus_signal", None)
_fig = build_chart(a, int(nb), show, live if show["refs"] else [], dark=svc.store.get_pref(svc.local_user, "dark", False))
_fig.update_layout(dragmode="zoom", newshape=dict(line_color="#f5a623", line_width=2))
st.plotly_chart(_fig, width="stretch", config={"modeBarButtonsToAdd": ["drawline", "drawopenpath", "drawrect", "drawcircle",
                                                                      "eraseshape"], "displaylogo": False,
                                               "scrollZoom": True})
st.caption("Drawing tools (toolbar: line, path, rectangle, circle, eraser) are for on-screen study only; drawings are not "
           "saved or used by any scanner.")
st.caption("All annotations come from computed events. Stop/target are rule-based reference levels — not trade instructions.")

L = a.last
m = st.columns(7)
for col, (lab, key, fmt) in zip(m, [("Close", "Close", "{:.2f}"), ("RSI14", "rsi14", "{:.1f}"), ("RVOL", "rvol", "{:.2f}"),
                                    ("ATR%", "atr_pct", "{:.2f}"), ("ADX", "adx", "{:.1f}"), ("MACD hist", "macd_hist", "{:.2f}"), ("VWAP", "vwap", "{:.2f}")]):
    col.metric(lab, num(L.get(key), fmt))
rg = classify_regime(a, cfg["analysis"]["compression_ratio"])
st.markdown(f"**Regime:** {rg['regime']} · volatility {rg['volatility']} — {'; '.join(rg['evidence'])}")

t1, t2, t3, t4, t5 = st.tabs(["Structure events", "Setups", "Multi-timeframe", "Signal history", "Data quality / export"])
with t1:
    T = a.time
    rows = ([{"time": T(e.i), "type": e.type, "detail": f"{e.direction}: close beyond {e.level:.2f} (swing {T(e.swing_i)})"} for e in a.events[-10:]]
            + [{"time": T(s.confirmed_at), "type": f"Swing {s.label}", "detail": f"{s.price:.2f} at {T(s.i)} (confirmed {T(s.confirmed_at)})"} for s in a.swings[-10:]]
            + [{"time": T(c_["i"]), "type": "Candle", "detail": f"{c_['pattern']} {c_['bias']} [{c_['status']}] · confirm {c_['confirm']} · invalid {c_['invalid']}"} for c_ in a.candles]
            + [{"time": T(s["i"]), "type": "Liquidity sweep", "detail": f"{s['side']} @ {s['level']:.2f} (price-behaviour hypothesis)"} for s in a.liquidity["sweeps"][-5:]])
    st.dataframe(pd.DataFrame(rows).sort_values("time", ascending=False), hide_index=True, width="stretch")
    st.markdown("**Patterns**")
    st.dataframe(pd.DataFrame([{k: v for k, v in p.items() if k != "points"} for p in a.patterns]) if a.patterns else pd.DataFrame({"info": ["none detected"]}), hide_index=True, width="stretch")
    st.markdown("**Zones & S/R**")
    st.dataframe(pd.DataFrame(a.zones + a.sr), hide_index=True, width="stretch")
with t2:
    sd = pd.DataFrame(sigs)
    if sd.empty:
        st.info("No strategy setup was evaluated.")
    else:
        st.dataframe(sd[["strategy", "setup", "timeframe", "direction", "status", "label", "score", "score_coverage", "rr", "entry_zone", "stop",
                         "target", "expiry", "conditions_passed", "conditions_failed", "score_breakdown", "warnings"]]
                     .sort_values("score", ascending=False), hide_index=True, width="stretch")
with t3:
    mt = []
    for t in tfs:
        x = an[t]
        mt.append({"timeframe": t, "data": reps[t].status, "last completed bar": reps[t].last, "trend": x.trend_label if x else "NA",
                   "trend score": round(x.trend_score, 2) if x and x.trend_score is not None else None,
                   "structure": x.structure_trend if x else "NA", "regime": classify_regime(x)["regime"] if x else "NA",
                   "signals": ", ".join(f"{s['strategy']}:{s['direction']}({s['status']})" for s in sigs if s["timeframe"] == t and s["status"] != "rejected") or "-"})
    mdf = pd.DataFrame(mt)
    st.dataframe(mdf, hide_index=True, width="stretch")
    labs = {r["trend"] for r in mt if r["trend"] not in ("NA",)}
    st.markdown("**Alignment:** " + ("aligned: " + labs.pop() if len(labs) == 1 else "⚠️ CONFLICT: " + ", ".join(sorted(labs))))
with t4:
    h = svc.store.signal_history(svc.local_user, tk, 100)
    st.dataframe(pd.DataFrame(h).drop(columns=["payload"], errors="ignore") if h else pd.DataFrame({"info": ["No stored signals for this ticker"]}),
                 hide_index=True, width="stretch")
with t5:
    st.dataframe(pd.DataFrame([r.as_row() for r in reps.values()]), hide_index=True, width="stretch")
    st.download_button("⬇️ OHLCV + indicators CSV", to_csv_bytes(a.df), export_name(f"{tk}_{tf}_ohlcv"), "text/csv")
    if sigs:
        st.download_button("⬇️ Setup evidence CSV", to_csv_bytes(pd.DataFrame(sigs)), export_name(f"{tk}_evidence"), "text/csv")

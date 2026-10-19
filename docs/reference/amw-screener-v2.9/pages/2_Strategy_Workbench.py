import copy
import importlib

import pandas as pd
import streamlit as st

from src.config import STRATEGIES, TIMEFRAMES, validate_settings
from src.screener.scanner import analyse_symbol
from src.strategies.base import StrategyContext
from src.strategies.registry import REGISTRY, required_timeframes
from ui_common import page

svc = page("Strategy Workbench", "🛠️")
cfg = svc.cfg()

tab_names = {"breakout": "Breakout", "pullback": "Pullback", "reversal": "Reversal",
             "trend_continuation": "Trend Continuation", "intraday": "Intraday", "swing": "Swing"}
name = st.radio("Strategy", STRATEGIES, format_func=tab_names.get, horizontal=True)
sc = copy.deepcopy(cfg["strategies"][name])
st.caption(f"Active version **v{sc['version']}** · The strategy version is stored with every signal.")
with st.expander("Rules (documentation from code)"):
    st.code(importlib.import_module(REGISTRY[name].__module__).__doc__ or "")

with st.container(border=True):
    st.markdown("#### Rule builder")
    c = st.columns(3)
    sc["enabled"] = c[0].toggle("Enabled", sc["enabled"])
    allowed = ["5m", "15m", "30m", "1H"] if name == "intraday" else TIMEFRAMES
    sc["timeframes"] = c[1].multiselect("Timeframes", allowed, [t for t in sc["timeframes"] if t in allowed])
    cols = st.columns(3)
    for i, (k, v) in enumerate(sc["params"].items()):
        col = cols[i % 3]
        key = f"{name}_{k}"
        if isinstance(v, bool):
            sc["params"][k] = col.checkbox(k, v, key=key)
        elif isinstance(v, int) and not isinstance(v, bool):
            sc["params"][k] = int(col.number_input(k, value=v, step=1, key=key))
        elif isinstance(v, float):
            sc["params"][k] = float(col.number_input(k, value=v, step=0.1, format="%.2f", key=key))
        elif isinstance(v, list):
            txt = col.text_input(k, ",".join(map(str, v)), key=key)
            try:
                sc["params"][k] = [type(v[0])(x.strip()) for x in txt.split(",") if x.strip()]
            except ValueError:
                col.error("Invalid list")
        elif k == "stop_method":
            sc["params"][k] = col.selectbox(k, ["structure", "atr"], ["structure", "atr"].index(v), key=key)
        else:
            sc["params"][k] = col.text_input(k, str(v), key=key)

new_cfg = copy.deepcopy(cfg)
new_cfg["strategies"][name] = sc
try:
    validate_settings(new_cfg)
    valid = True
except ValueError as e:
    st.error(f"Invalid configuration: {e}")
    valid = False

st.markdown("#### Preview (before saving)")
c = st.columns([2, 1])
syms = c[0].text_input("Preview symbols", "RELIANCE.NS, TCS.NS, INFY.NS")
run = c[1].button("👁 Run preview", disabled=not valid)
if run:
    tickers = [s.strip().upper() for s in syms.split(",") if s.strip()]
    tickers = [t if t.endswith(".NS") or t.startswith("^") else t + ".NS" for t in tickers]
    rows = []
    with st.spinner("Evaluating with current and new rules…"):
        for label, c_ in (("current", cfg), ("preview", new_cfg)):
            strat = REGISTRY[name](c_)
            tfs = required_timeframes([strat])
            prov = svc.provider(c_)
            for t in tickers:
                raw = {tf: prov.fetch_many([t], tf).get(t) for tf in tfs}
                an, reps = analyse_symbol(raw, t, tfs, c_, data_kind=prov.data_kind)
                for s in strat.evaluate(StrategyContext(t, t, an, reps, {}, c_)) if c_["strategies"][name]["enabled"] else []:
                    r = s.to_row()
                    rows.append({"rules": label, **{k: r[k] for k in ("ticker", "setup", "timeframe", "direction", "status", "label", "score",
                                                                     "conditions_passed", "conditions_failed")}})
    if rows:
        pv = pd.DataFrame(rows)
        st.dataframe(pd.crosstab([pv.ticker, pv.direction], [pv.rules, pv.status]), width="stretch")
        st.dataframe(pv, hide_index=True, width="stretch")
    else:
        st.info("Neither rule set evaluated a setup (trend pre-filter / data).")

c = st.columns([3, 1])
note = c[0].text_input("Version note")
if c[1].button("💾 Save new version", type="primary", disabled=not valid):
    v = svc.store.save_strategy_config(name, sc, note, actor="local")
    st.success(f"{name} v{v} saved and activated.")
    st.rerun()

st.markdown("#### Version history")
hist = svc.store.strategy_versions(name)
if hist:
    h = pd.DataFrame(hist)
    st.dataframe(h[["version", "active", "note", "created"]], hide_index=True)
    c = st.columns(2)
    v = c[0].selectbox("Version", [x["version"] for x in hist])
    if c[1].button("Activate"):
        svc.store.activate_version(name, v, actor="local")
        st.rerun()
else:
    st.caption("No version in the database yet — defaults from `config/settings.yaml` are in use.")

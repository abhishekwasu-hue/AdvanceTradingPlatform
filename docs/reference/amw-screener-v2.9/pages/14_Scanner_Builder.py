import json

import pandas as pd
import streamlit as st

from src.lab.ir import render_strategy
from src.scanner import nl, replay, spec, templates
from ui_common import page

svc = page("Scanner Builder", "🧩")
uid = svc.local_user
st.caption("Build a scanner from a template or plain language. Every scanner is a validated specification (shared rule "
           "engine); the deterministic engine — never the AI — decides which stocks qualify. Results show PASS / FAIL / "
           "UNKNOWN per condition. Templates are starting points with NO validated predictive value.")
t1, t2, t3 = st.tabs(["Templates (beginner)", "Natural language (advanced)", "My scanners"])


def save_box(name_default: str, ir: dict, envelope: dict, key: str):
    c = st.columns([3, 1])
    name = c[0].text_input("Scanner name", name_default, key=f"n_{key}")
    if c[1].button("💾 Save as scanner", key=f"s_{key}", type="primary"):
        try:
            r = spec.save(svc, uid, name, ir, envelope)
            st.success(f"Saved {name} v{r['version']} (id {r['scanner_id'][:8]}). Open 'My scanners' to run or replay it.")
        except (ValueError, PermissionError) as e:
            st.error(str(e))


with t1:
    lst = templates.listing()
    cat = st.radio("Category", templates.CATEGORIES, horizontal=True)
    opts = [t for t in lst if t["category"] == cat]
    tid = st.selectbox("Template", [t["id"] for t in opts], format_func=lambda i: next(t["name"] for t in opts if t["id"] == i))
    meta = next(t for t in opts if t["id"] == tid)
    tp = templates.get(tid)
    st.code(render_strategy(tp["ir"]), language=None)
    st.caption(f"Timeframes: {', '.join(meta['timeframes'])} · version {meta['version']} · {meta['data_requirements']}")
    st.warning("Limitations: " + "; ".join(meta["limitations"]) + f". Evidence: {meta['evidence']}.")
    save_box(tp["name"], tp["ir"], tp["envelope"], "tpl")

with t2:
    text = st.text_area("Describe the scanner", "Find Nifty 200 stocks with a bullish weekly trend, daily close above EMA 50, "
                        "RSI above 50 and relative volume above 1.5", height=90)
    if st.button("Parse", type="primary"):
        st.session_state.nl = nl.parse(text)
    r = st.session_state.get("nl")
    if r:
        st.markdown("**Interpretation (review before saving)**")
        for n in r["interpretations"]:
            st.markdown(f"- {n}")
        for u in r["unsupported"]:
            st.error(f"Not supported (not added, not replaced by anything similar): {u}")
        for e in r["errors"]:
            st.error(e)
        if r["readable"]:
            st.code(r["readable"], language=None)
        with st.expander("Edit the specification (JSON)"):
            ir_txt = st.text_area("Rules (IR)", json.dumps(r["ir"], indent=2), height=260)
            env_txt = st.text_area("Envelope", json.dumps(r["envelope"], indent=2), height=110)
        try:
            ir_e, env_e = json.loads(ir_txt), json.loads(env_txt)
            chk = spec.build("preview", ir_e, env_e)
            if chk["errors"]:
                st.error("Validation: " + "; ".join(chk["errors"]))
            else:
                st.success("Specification is valid.")
                save_box("My NL scanner", ir_e, env_e, "nl")
        except json.JSONDecodeError as e:
            st.error(f"JSON error: {e}")

with t3:
    mine = [s for s in svc.store.lab_list(uid) if spec.is_scanner(s)]
    if not mine:
        st.info("No scanners yet — save one from a template or the natural-language tab.")
    else:
        sid = st.selectbox("Scanner", [s["id"] for s in mine], format_func=lambda i: next(s["name"] for s in mine if s["id"] == i))
        vs = svc.store.lab_versions(uid, sid)
        envd = (next(s for s in mine if s["id"] == sid)["draft_spec"] or {}).get("scanner", {})
        st.caption(f"{len(vs)} version(s) · timeframe {envd.get('timeframe')} · universe {envd.get('universe')} · "
                   f"incomplete candles: {envd.get('incomplete_candle_policy')}")
        if vs:
            st.code(render_strategy(vs[0]["ir"]), language=None)
        syms = st.text_input("Symbols (comma separated)", "RELIANCE.NS, TCS.NS, INFY.NS, HDFCBANK.NS, ICICIBANK.NS")
        tickers = [x.strip().upper() for x in syms.split(",") if x.strip()]
        c = st.columns(3)
        as_of = c[0].text_input("Replay as of (exchange time; empty = now)", "")
        ver = c[1].selectbox("Version", [v["version"] for v in vs] or [None])
        if c[2].button("▶ Run / replay", disabled=not vs):
            with st.spinner("Evaluating completed bars…"):
                try:
                    res = replay.replay(svc, uid, sid, as_of or str(pd.Timestamp.now(tz="Asia/Kolkata")), tickers, ver)
                    st.session_state.replay = res
                except (ValueError, KeyError) as e:
                    st.error(str(e))
        res = st.session_state.get("replay")
        if res and res["scanner_id"] == sid:
            st.markdown(f"**{res['qualified']} qualified of {res['evaluated']} evaluated** · as of {res['as_of']} · v{res['version']}")
            st.caption(res["method"] + ". Limitations: " + "; ".join(res["limitations"]))
            for x in res["results"]:
                head = f"{x['ticker']} · {x['status']}" + (f" · signal bar {x.get('signal_bar')}" if x.get("signal_bar") else "")
                with st.expander(head):
                    if x.get("reason"):
                        st.write(x["reason"])
                    if x["conditions"]:
                        st.dataframe(pd.DataFrame(x["conditions"]), hide_index=True, width="stretch")
            if res["retrospective"]:
                st.markdown("**Retrospective outcomes (later bars — not part of the decision)**")
                st.dataframe(pd.DataFrame(res["retrospective"]), hide_index=True, width="stretch")
        st.divider()
        c = st.columns([2, 1])
        nm = c[0].text_input("Duplicate as", "")
        if c[1].button("Duplicate", disabled=not nm):
            spec.duplicate(svc, uid, sid, nm)
            st.rerun()
        c = st.columns([2, 1])
        to = c[0].text_input("Share a copy of the rules with user (username)", "",
                             help="Only the rules and settings are copied; no signals, data or credentials.")
        if c[1].button("Share", disabled=not to):
            try:
                spec.share(svc, uid, sid, to.strip())
                st.success(f"Copy sent to {to.strip()} (as a draft they can review and publish).")
            except KeyError as e:
                st.error(str(e))
        if len(vs) >= 2:
            st.markdown("**Compare two versions on the same data**")
            cc = st.columns(3)
            va = cc[0].selectbox("Version A", [v["version"] for v in vs], index=1)
            vb = cc[1].selectbox("Version B", [v["version"] for v in vs], index=0)
            if cc[2].button("Compare (max 10 symbols)"):
                with st.spinner("Backtesting both versions with the same engine and costs…"):
                    try:
                        cmpr = replay.compare_versions(svc, uid, sid, va, vb, tickers[:10])
                        st.dataframe(pd.DataFrame(cmpr["versions"]).T, width="stretch")
                        st.caption(cmpr["methodology"])
                        st.info(cmpr["verdict"])
                    except (ValueError, KeyError) as e:
                        st.error(str(e))
st.caption("Developer mode: write scanners in Python, Pine (subset), JavaScript, TypeScript, SQL, Lua or R in the "
           "Strategy Lab page; they use the same contract, versioning and sandbox.")

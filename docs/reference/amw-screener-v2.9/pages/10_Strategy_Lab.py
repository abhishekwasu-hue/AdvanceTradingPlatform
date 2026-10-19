import copy
import json
import time

import pandas as pd
import streamlit as st

from src.config import TIMEFRAMES
from src.data.validation import clean_and_validate, usable
from src.lab import engine
from src.lab.contract import LANGUAGES, StrategySpec, capabilities, enabled_languages
from src.lab.ir import PREDICATES, TEMPLATES as IR_T, NotExportable, export_python, lookback_required, render_strategy
from src.lab.pine import TEMPLATES as PINE_T, __doc__ as PINE_DOC
from src.lab.service import lab_backtest, run_on_symbols, start_lab_scan
from src.lab.sqlcheck import TEMPLATES as SQL_T
from src.lab.templates import CODE_TEMPLATES
from ui_common import page

try:
    from streamlit_ace import st_ace
except ImportError:  # pragma: no cover
    st_ace = None

svc = page("Strategy Lab", "🧪")
uid = svc.local_user
store = svc.store
NAMES = {"python": "Python", "pine": "Pine Script (subset)", "javascript": "JavaScript", "typescript": "TypeScript", "sql": "SQL screener",
         "lua": "Lua", "r": "R", "visual": "Visual No-Code Builder"}
ACE = {"python": "python", "pine": "javascript", "javascript": "javascript", "typescript": "typescript", "sql": "sql", "lua": "lua", "r": "r"}
TEMPL = {"python": CODE_TEMPLATES["python"], "javascript": CODE_TEMPLATES["javascript"], "typescript": CODE_TEMPLATES["typescript"],
         "lua": CODE_TEMPLATES["lua"], "r": CODE_TEMPLATES["r"], "pine": PINE_T, "sql": SQL_T, "visual": IR_T}


@st.cache_data(ttl=600, show_spinner=False)
def caps():
    return capabilities()


cap = caps()
ENABLED = enabled_languages(svc.cfg())
st.markdown("Build your own screener with the **Visual builder** (no code), **Pine Script**, **Python**, **SQL**, **JavaScript**, "
            "**TypeScript**, **Lua** or **R** → **Validate** → **Run** (on a few stocks) → "
            "**Publish** (immutable version) → **Scan** a full universe or **Backtest**.")
disabled = [NAMES[x] for x in LANGUAGES if x not in ENABLED]
if disabled:
    st.caption("Disabled by the operator on this server: " + ", ".join(disabled))
not_runnable = [NAMES[x] for x in ENABLED if x not in ("visual", "pine") and cap[x]["native_execution"] is not True]
if not_runnable:
    st.caption("Editable here but cannot execute on this machine (runtime missing or no sandbox): " + ", ".join(not_runnable)
               + " — see the capability table.")
with st.expander("📘 Getting started"):
    st.markdown("""
**Pine Script (TradingView-style, easiest):**
1. *New strategy* → mode **Pine Script** → pick a template (e.g. *EMA trend*).
2. The line `long_signal = ...` (and optionally `short_signal = ...`) is your screening condition.
3. Adjust stop/target with `stop_atr = input.float(1.5)` and `target_r = input.float(2.0)`.
4. Only the supported subset runs (see *Supported Pine subset* below). `strategy.entry`, `if`, `request.security`, etc. are rejected with a reason.

**Visual builder (no programming):** pick a template (e.g. *Bullish trend*), add conditions with *Add condition*
(comparison, cross, or structure/MTF/session/liquidity predicates), group them with AND/OR, set stop/target. The readable
logic is shown below the builder.

**SQL:** one `SELECT` over the views `bars` (all bars) and `latest` (last completed bar) returning a `symbol` column;
a returned row = qualified. Parameters as `:name`.

**JavaScript / TypeScript / Lua / R:** write `evaluate(ctx)` returning the same result object; TypeScript is type-checked
against the AMW SDK (`Context`, `Result`, `amw`). Lua arrays are 1-based and missing values are NaN; R missing values are NA.

**Python (most flexible):**
1. Write `def evaluate(ctx):` returning `{"status": "qualified"/"rejected", "bias": "long"/"short"/"neutral", "rules": [...]}`.
2. `ctx["series"]["close"][-1]` is the close of the last **completed** candle. `None` = no data (warm-up).
3. Use helpers such as `amw.last(x)`, `amw.highest(x, n)`, `amw.crossover(a, b)`. Only `math` and `statistics` can be imported.
4. No access to network, files or secrets — code runs in an isolated sandbox.

**Important:** *Validate* only checks the code — it does not mean the strategy ran. Only *Run* results reflect real execution.
""")
with st.expander("Authoring modes — actual capabilities (detected on this machine)", expanded=False):
    rows = [{"mode": NAMES[k], "release": "Released ✅" if k in ENABLED else "Disabled by operator", **{c: ("✅" if v is True else "❌" if v is False else str(v)) for c, v in cap[k].items() if c != "limitations"},
             "limitations": cap[k]["limitations"]} for k in LANGUAGES]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    sb = cap["sandbox_status"]
    (st.success if sb["verified"] else st.error)(
        f"Sandbox {'VERIFIED' if sb['verified'] else 'NOT verified'} on {sb['platform']}: " + json.dumps(sb.get("checks") or sb.get("reason")))
    if not sb["verified"]:
        if sb["unsandboxed_allowed"]:
            st.warning("Local trusted mode (AMW_ALLOW_UNSANDBOXED=1): Python runs without an OS-level sandbox — run only your own code.")
        else:
            st.warning("No sandbox on this machine, so Python execution is disabled. Pine Script works everywhere. "
                       "For Python: use `start_screener.bat` on Windows (local trusted mode) or Docker (full sandbox).")

# ---------------- strategy picker
mine = store.lab_list(uid)
c = st.columns([3, 2, 1, 1])
names = ["➕ New strategy"] + [f"{s['name']} · {NAMES[s['language']]}" for s in mine]
ids = [None] + [s["id"] for s in mine]
want = st.session_state.pop("lab_sid", None)
if want is not None and want in ids:
    st.session_state["lab_current"] = want
cur = st.session_state.get("lab_current")
pick = c[0].selectbox("Strategy", names, index=ids.index(cur) if cur in ids else 0)
st.session_state["lab_current"] = ids[names.index(pick)]
if pick == "➕ New strategy":
    with st.container(border=True):
        n1, n2 = st.columns(2)
        nm = n1.text_input("Name")
        lang = n2.selectbox("Authoring mode", ENABLED, format_func=NAMES.get)
        tpl = st.selectbox("Start from template", ["(blank)"] + list(TEMPL[lang]), key=f"tpl_{lang}")
        if st.button("Create draft", type="primary", disabled=not nm.strip()):
            src = ir = None
            if lang == "visual":
                ir = copy.deepcopy(IR_T.get(tpl, IR_T["Bullish trend"]))
            else:
                src = TEMPL[lang].get(tpl, "")
            spec = StrategySpec(language=lang, htf=["1W"] if "Multi" in tpl else [],
                                params=[{"name": k, "type": "float", "default": v} for k, v in
                                        {"min_adx": 20, "rsi_lo": 40, "rsi_hi": 70, "min_rvol": 1.5}.items()] if lang == "sql" else [])
            from src.saas.entitlements import EntitlementError, check
            created = False
            try:
                check(store, uid, "lab_strategies", len(store.lab_list(uid)) + 1)
                st.session_state.lab_sid = store.lab_create(uid, nm, lang, "", src, ir, spec.model_dump())
                created = True
            except EntitlementError as e:
                st.error(str(e))
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not create ({type(e).__name__}) — is the name already used?")
            if created:
                st.rerun()
    up = st.file_uploader("Import strategy (.json exported from the Lab)", type=["json"])
    if up is not None:
        try:
            d = json.loads(up.read().decode("utf-8"))
            spec = StrategySpec.model_validate(d["spec"])
            v = engine.validate(d["language"], d.get("source"), d.get("ir"), spec, f"user{uid}")
            sid = store.lab_create(uid, d["name"] + " (imported)", d["language"], d.get("description", ""), d.get("source"), d.get("ir"), spec.model_dump())
            st.success(f"Imported as draft (validation: {'ok' if v['ok'] else 'FAILED: ' + '; '.join(v['errors'])}). Not executed.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Import rejected: {e}")
    st.stop()

S = mine[names.index(pick) - 1]
sid, lang = S["id"], S["language"]
if lang not in ENABLED:
    st.warning(f"{NAMES[lang]} is disabled by the operator on this server — the code is saved, but validate/run/scan are unavailable.")
spec = StrategySpec.model_validate(S["draft_spec"] or {"language": lang})
c[1].markdown(f"**Mode:** {NAMES[lang]}  \n**Versions:** {len(store.lab_versions(uid, sid))}")
if c[2].button("Duplicate"):
    store.lab_create(uid, f"{S['name']} copy {int(time.time()) % 10000}", lang, S["description"], S["draft_source"], S["draft_ir"], S["draft_spec"])
    st.rerun()
c[3].download_button("Export", json.dumps({"name": S["name"], "language": lang, "description": S["description"], "source": S["draft_source"],
                                           "ir": S["draft_ir"], "spec": S["draft_spec"]}, indent=2), f"{S['name']}.json", "application/json")
if not cap[lang]["native_execution"] and lang not in ("visual", "pine"):
    st.warning(f"{NAMES[lang]}: execution is NOT available on this machine — you can edit and save; validation may also be unavailable.")

# ---------------- spec
with st.expander("Strategy specification (timeframes, history, parameters, limits)"):
    s1 = st.columns(4)
    tfs = s1[0].multiselect("Timeframes", TIMEFRAMES, spec.timeframes)
    htf = s1[1].multiselect("Higher-TF features", ["1W", "1D", "1H", "30m", "15m"], spec.htf)
    lb = s1[2].number_input("Lookback bars (incl. warm-up)", 30, 1000, spec.lookback)
    mb = s1[3].number_input("Min valid bars", 1, 1000, spec.min_bars)
    s2 = st.columns(4)
    dq = s2[0].selectbox("Data quality", ["OK_only", "OK_or_STALE"], ["OK_only", "OK_or_STALE"].index(spec.data_quality))
    to = s2[1].number_input("Timeout (s)", 1.0, 120.0, float(spec.limits.timeout_s))
    mem = s2[2].number_input("Memory (MB)", 32, 2048, spec.limits.memory_mb)
    params_txt = st.text_area("Parameters (JSON list: name, type int|float|bool|str, default, min, max)",
                              json.dumps([p.model_dump() for p in spec.params]))
    try:
        new_spec = StrategySpec.model_validate({**spec.model_dump(), "timeframes": tfs or ["1D"], "htf": htf, "lookback": int(lb),
                                                "min_bars": int(mb), "data_quality": dq, "params": json.loads(params_txt or "[]"),
                                                "limits": {"timeout_s": to, "memory_mb": int(mem), "max_output_kb": spec.limits.max_output_kb}})
    except Exception as e:  # noqa: BLE001
        st.error(f"Invalid spec: {e}")
        new_spec = spec

# ---------------- editor / builder
source, ir = S["draft_source"], copy.deepcopy(S["draft_ir"])
if lang == "visual":
    st.markdown("#### Visual rule builder")
    ir = ir or copy.deepcopy(IR_T["Bullish trend"])
    side = st.radio("Edit side", ["long", "short"], horizontal=True)
    grp = ir.get(side) or {"type": "group", "logic": "AND", "children": []}
    grp["logic"] = st.radio("Combine top-level rules with", ["AND", "OR"], ["AND", "OR"].index(grp["logic"]), horizontal=True, key=f"lg{side}")
    for i, ch in enumerate(list(grp["children"])):
        cc = st.columns([6, 1, 1, 1])
        from src.lab.ir import render_group
        cc[0].code(render_group(ch), language=None)
        if cc[1].button("↑", key=f"u{side}{i}", disabled=i == 0):
            grp["children"][i - 1], grp["children"][i] = grp["children"][i], grp["children"][i - 1]
            store.lab_save_draft(uid, sid, None, {**ir, side: grp}, new_spec.model_dump())
            st.rerun()
        if cc[2].button("↓", key=f"d{side}{i}", disabled=i == len(grp["children"]) - 1):
            grp["children"][i + 1], grp["children"][i] = grp["children"][i], grp["children"][i + 1]
            store.lab_save_draft(uid, sid, None, {**ir, side: grp}, new_spec.model_dump())
            st.rerun()
        if cc[3].button("🗑", key=f"x{side}{i}"):
            grp["children"].pop(i)
            store.lab_save_draft(uid, sid, None, {**ir, side: grp if grp["children"] else None}, new_spec.model_dump())
            st.rerun()
    with st.form(f"add{side}"):
        st.markdown("**Add condition**")
        kind = st.radio("Type", ["Comparison", "Cross", "Predicate (structure / MTF / session / liquidity)"], horizontal=True)
        fields = ["close", "open", "high", "low", "volume", "hl2", "ema20", "ema50", "ema200", "sma20", "sma50", "rsi14", "macd",
                  "macd_signal", "macd_hist", "atr14", "adx", "plus_di", "minus_di", "rvol", "vwap", "bb_up", "bb_low"]

        def operand(prefix):
            cc = st.columns(4)
            k = cc[0].selectbox(f"{prefix} type", ["field", "indicator", "number"], key=prefix + "k")
            if k == "number":
                return {"t": "num", "v": float(cc[1].number_input(f"{prefix} value", value=50.0, key=prefix + "v"))}
            f = cc[1].selectbox(f"{prefix} field", fields, key=prefix + "f")
            off = int(cc[3].number_input(f"{prefix} bars ago", 0, 100, 0, key=prefix + "o"))
            e = {"t": "field", "name": f}
            if k == "indicator":
                fn = cc[2].selectbox(f"{prefix} indicator", ["ema", "sma", "rsi", "highest", "lowest", "change", "stdev", "atr"], key=prefix + "i")
                n = int(st.number_input(f"{prefix} period", 1, 500, 20, key=prefix + "n"))
                e = {"t": "ind", "fn": fn, "src": None if fn == "atr" else e, "n": n}
            return {"t": "shift", "a": e, "n": off} if off else e
        if kind == "Predicate (structure / MTF / session / liquidity)":
            pn = st.selectbox("Predicate", list(PREDICATES), format_func=lambda k: f"{k} — {PREDICATES[k]['desc']}")
            args = st.text_input("Arguments (JSON)", json.dumps(PREDICATES[pn]["args"]))
            expr = {"t": "pred", "name": pn, "args": json.loads(args or "{}")}
        else:
            a = operand("Left")
            op = st.selectbox("Operator", [">", "<", ">=", "<=", "==", "!="]) if kind == "Comparison" else st.selectbox("Cross", ["over", "under"])
            b = operand("Right")
            expr = {"t": "cmp", "op": op, "a": a, "b": b} if kind == "Comparison" else {"t": "cross", "dir": op, "a": a, "b": b}
        rname = st.text_input("Rule name", "")
        nest = st.checkbox("Add as a nested OR-group with the previous rule")
        if st.form_submit_button("➕ Add"):
            from src.lab.ir import render
            rule = {"type": "rule", "name": rname or render(expr), "expr": expr}
            if nest and grp["children"]:
                prev = grp["children"].pop()
                rule = {"type": "group", "logic": "OR", "children": [prev, rule]}
            grp["children"].append(rule)
            store.lab_save_draft(uid, sid, None, {**ir, side: grp}, new_spec.model_dump())
            st.rerun()
    ir[side] = grp if grp["children"] else None
    sc = st.columns(3)
    stop_m = sc[0].selectbox("Stop", ["atr", "swing", "none"], ["atr", "swing", "none"].index((ir.get("stop") or {"method": "none"})["method"]))
    mult = sc[1].number_input("ATR mult / swing buffer", 0.1, 10.0, float((ir.get("stop") or {}).get("mult", (ir.get("stop") or {}).get("buffer_atr", 1.5))))
    tr = sc[2].number_input("Target (R, 0 = none)", 0.0, 20.0, float((ir.get("target") or {"r": 0}).get("r", 0)))
    ir["stop"] = None if stop_m == "none" else ({"method": "atr", "mult": mult} if stop_m == "atr" else {"method": "swing", "buffer_atr": mult})
    ir["target"] = {"r": tr} if tr > 0 else None
    st.markdown("**Generated logic (readable)**")
    st.code(render_strategy(ir), language=None)
    st.caption(f"Required history incl. warm-up: {lookback_required(ir)} bars (spec lookback {new_spec.lookback}). Missing data → rule = missing → not qualified.")
    with st.expander("Intermediate representation (JSON) / export"):
        st.json(ir)
        try:
            st.code(export_python(ir), language="python")
            st.caption("Python export: semantic equivalence with the visual engine is verified by automated tests for the expression subset.")
        except NotExportable as e:
            st.info(f"Not exportable to code: {e}")
else:
    if lang == "pine":
        with st.expander("Supported Pine subset"):
            st.code(PINE_DOC)
    if lang == "python":
        with st.expander("Python quick reference (ctx and amw helpers)"):
            st.code("""ctx["meta"]       -> symbol, timeframe, last_completed_bar, data_status, ...
ctx["series"]     -> time, open, high, low, close, volume, ema20, ema50, ema200, sma20, sma50,
                     rsi14, macd, macd_signal, macd_hist, atr14, adx, plus_di, minus_di, rvol, vwap, bb_up, bb_low
                     (list; [-1] = last completed bar; None = missing)
ctx["structure"]  -> trend_label, structure_trend, swings[], events[] (BOS/CHoCH), zones[], sr[]
ctx["htf"]["1W"]  -> higher-timeframe trend (if added in the spec)
ctx["params"]     -> your parameters
amw.last(s, n=0)  amw.highest(s, n)  amw.lowest(s, n)  amw.crossover(a, b)  amw.crossunder(a, b)
amw.ema(s, n)  amw.sma(s, n)  amw.rsi(s, n)  amw.atr(high, low, close, n)  amw.isna(x)
return {"status": "qualified"|"rejected", "bias": "long"|"short"|"neutral",
        "rules": [{"name": "...", "passed": True|False|None}], "stop": float, "target": float}""", language="python")
    dark = store.get_pref(uid, "dark", False)
    if st_ace:
        source = st_ace(value=S["draft_source"] or "", language=ACE[lang], theme="monokai" if dark else "chrome", height=420,
                        auto_update=True, show_gutter=True, wrap=False, key=f"ace_{sid}", font_size=14)
    else:
        source = st.text_area("Source", S["draft_source"] or "", height=420)

# ---------------- actions
b = st.columns(6)
if b[0].button("💾 Save draft"):
    store.lab_save_draft(uid, sid, None if lang == "visual" else source, ir if lang == "visual" else None, new_spec.model_dump())
    st.success("Draft saved (not executed).")
save_as = b[1].text_input("Save as", label_visibility="collapsed", placeholder="Save as name")
if b[1].button("Save As") and save_as:
    store.lab_create(uid, save_as, lang, S["description"], None if lang == "visual" else source, ir if lang == "visual" else None, new_spec.model_dump())
    st.rerun()
if b[2].button("✔ Validate"):
    v = engine.validate(lang, source, ir, new_spec, f"user{uid}")
    (st.success if v["ok"] else st.error)(f"{'Valid' if v['ok'] else 'Invalid'} — stage: {v['stage']}. Validation is NOT execution.")
    for e in v["errors"]:
        st.error(e)
    for w in v.get("warnings", []):
        st.warning(w)
note = b[3].text_input("Version note", label_visibility="collapsed", placeholder="version note")
if b[3].button("📌 Publish version"):
    store.lab_save_draft(uid, sid, None if lang == "visual" else source, ir if lang == "visual" else None, new_spec.model_dump())
    v = engine.validate(lang, source, ir, new_spec, f"user{uid}")
    if v["ok"]:
        st.success(f"Published immutable v{store.lab_publish(uid, sid, note)}")
    else:
        st.error("Validation failed — not published: " + "; ".join(v["errors"]))

st.markdown("#### Run (draft preview on live validated data)")
r1 = st.columns([3, 1, 1])
tick = r1[0].text_input("Symbols", "RELIANCE.NS, TCS.NS, INFY.NS")
job_id = st.session_state.get("lab_run_job")
job = svc.jobs.get(job_id) if job_id else None
running = job is not None and job.status in ("queued", "running")
if r1[1].button("▶ Run", disabled=running):
    tks = [t.strip().upper() for t in tick.split(",") if t.strip()]
    tks = [t if t.endswith(".NS") or t.startswith("^") else t + ".NS" for t in tks]
    snap_src, snap_ir, snap_spec = (None if lang == "visual" else source), ir, new_spec

    def fn(progress, should_stop, job):
        return run_on_symbols(svc, uid, lang, snap_src, snap_ir, snap_spec, tks, cancel=job.cancel_event)
    st.session_state.lab_run_job = svc.jobs.submit("lab_run", fn, owner=uid).id
    st.rerun()
if r1[2].button("⏹ Stop", disabled=not running):
    svc.jobs.cancel(job_id)


@st.fragment(run_every=1 if running else None)
def run_box():
    j = svc.jobs.get(st.session_state.get("lab_run_job", ""))
    if j is None:
        st.caption("No run yet.")
        return
    if j.status in ("queued", "running"):
        st.info(f"{j.status}…")
        return
    if j.status == "failed":
        st.error(j.error)
        return
    rows = j.result or []
    if j.status == "cancelled":
        st.warning("Cancelled.")
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df.drop(columns=["logs"], errors="ignore"), hide_index=True, width="stretch")
        logs = "\n".join(f"[{r['ticker']} {r['timeframe']}] {r.get('logs')}" for r in rows if r.get("logs"))
        if logs:
            st.code(logs, language=None)
        st.caption("A row is 'qualified' only if the strategy executed AND its output passed schema validation.")
    elif j.status == "done":
        st.info("No results (all symbols lacked usable data?).")
run_box()

st.markdown("#### Scan & backtest (published versions only)")
vers = store.lab_versions(uid, sid)
if not vers:
    st.info("Publish a version to scan or backtest. Drafts are never executed by scans/backtests.")
else:
    k = st.columns(3)
    ver = k[0].selectbox("Version", [v["version"] for v in vers])
    uname = k[1].selectbox("Universe", list(svc.cfg()["universe"]["sources"]) + ["Custom"])
    lim = k[2].number_input("First N symbols", 1, 3000, 10)
    cs = st.text_input("Custom symbols (Custom universe)", "RELIANCE, TCS, INFY")
    k2 = st.columns(3)
    if k2[0].button("🔎 Start scan"):
        uni = svc.universe(uname, cs)
        try:
            j, rid = start_lab_scan(svc, uid, sid, ver, uni.symbols.head(int(lim)), uni.name)
            st.success(f"Scan job {j.id} (run #{rid}) queued — results in Signal Intelligence / run history below.")
        except Exception as e:  # noqa: BLE001
            st.error(str(e))
    btk = k2[1].text_input("Backtest ticker", "RELIANCE.NS")
    if k2[2].button("🧪 Backtest (1D)"):
        cfg = svc.cfg()
        prov = svc.provider(cfg)
        raw = prov.fetch_many([btk], "1D").get(btk)
        clean, rep = clean_and_validate(raw, btk, "1D", cfg)
        if not usable(rep):
            st.error(f"data {rep.status}")
        else:
            with st.spinner("Building point-in-time contexts and evaluating in one sandboxed batch…"):
                r = lab_backtest(svc, uid, sid, ver, clean, "1D")
            st.write(f"Status {r['run_status']} · compatibility: {r['compatibility']} · runtime {r['runtime']} · "
                     f"evaluation {r['evaluation_seconds']}s · errors {r['evaluation_errors']}")
            if str(r["all"].get("evidence", "")).startswith("INSUFFICIENT"):
                st.warning(f"{r['all']['evidence']} — not a validated edge.")
            st.dataframe(pd.DataFrame({x: r[x] for x in ("all", "in_sample", "out_of_sample")}).T, width="stretch")
            st.dataframe(r["trades"], hide_index=True, width="stretch")
    st.markdown("**Run history**")
    runs = store.lab_runs(uid, sid, 30)
    if runs:
        st.dataframe(pd.DataFrame(runs).drop(columns=["logs"], errors="ignore"), hide_index=True, width="stretch")

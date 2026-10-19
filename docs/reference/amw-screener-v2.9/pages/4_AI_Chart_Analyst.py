import datetime

import streamlit as st

from src.ai.agent import draft_strategy
from src.ai.gateway import NOTICE, AuthorizationRequired, BudgetExceeded, GatewayLLM
from src.ai.providers import LABEL, ProviderError
from src.analysis.regime import classify_regime
from src.config import TIMEFRAMES
from src.screener.scanner import analyse_symbol
from src.services.analyst import SYSTEM, analyse, build_evidence, validate_llm_text
from src.strategies.base import StrategyContext
from src.strategies.registry import enabled_strategies, evaluate_all
from ui_common import page

svc = page("AI Chart Analyst", "🤖")
cfg = svc.cfg()
prov = svc.provider(cfg)
uid = svc.local_user
gw = svc.ai
conf = gw.configured(uid)
pol = gw.policy(uid)

st.markdown("The analysis is based on **computed evidence** and always works without AI. An optional AI explanation uses "
            "**your own** OpenAI or Anthropic key (configure it in **AI Provider Setup & Usage**). The AI cannot change status, "
            "score or risk; if it cites a number that is not in the evidence, its answer is rejected.")
if conf:
    st.caption(NOTICE)
else:
    st.info("No AI provider key configured — showing deterministic analysis only.")

c = st.columns([2, 2, 1, 1])
tk = c[0].text_input("Ticker", st.session_state.get("detail_ticker", "RELIANCE.NS")).strip().upper()
tfs = c[1].multiselect("Timeframes (higher → lower)", TIMEFRAMES, ["1W", "1D", "1H"])
use_llm = c[2].toggle("AI explanation", False, disabled=not conf)
choice = None
if use_llm and (pol["selection"] == "manual" or (len(conf) > 1 and not pol["default_provider"])):
    choice = c[3].selectbox("Provider", list(conf), format_func=LABEL.get)
q = st.text_area("Question (optional)", placeholder="e.g. Which setup is stronger, the breakout or the pullback, and why?")


@st.cache_data(ttl=300, show_spinner=False)
def evidence(tk: str, tfs: tuple):
    an, reps = analyse_symbol({t: prov.fetch_many([tk], t).get(tk) for t in tfs}, tk, list(tfs), cfg, data_kind=prov.data_kind)
    sigs = [s.to_row() for s in evaluate_all(StrategyContext(tk, tk, an, reps, {}, cfg), enabled_strategies(cfg))]
    prim = an.get("1D") or next((x for x in an.values() if x), None)
    rg = classify_regime(prim) if prim else None
    if rg:
        rg.pop("rules", None)
    return build_evidence(tk, an, reps, sigs, rg)


if st.button("🔍 Analyse", type="primary") and tfs:
    with st.spinner("Building evidence…"):
        ev = evidence(tk, tuple(tfs))
    client = None
    if use_llm:
        try:
            b = gw.new_task(uid, "chart_question" if q else "chart_explanation", expected_calls=1)
            client = GatewayLLM(gw, uid, b, choice)
        except BudgetExceeded as e:
            st.warning(f"AI call not made — {e}. Showing the deterministic report.")
    r = analyse(ev, q or None, client, lang="English", retries=0)
    st.markdown(r.deterministic)
    if client is not None:
        st.divider()
        last = client.last
        st.markdown(f"**AI status:** {r.llm_status}" + (f" · {LABEL.get(last.provider, last.provider)} `{last.model}`" if last else ""))
        if r.llm_text:
            st.markdown(r.llm_text)
        if r.rejected_numbers:
            st.error(f"AI answer rejected: numbers not present in the evidence {r.rejected_numbers}")
        if last and not last.ok:
            st.error(last.message)
            for o in last.options:
                st.caption(f"• {o}")
        if last and last.est_cost_usd:
            st.caption(f"AMW estimate for this request: ${last.est_cost_usd:.5f} (your provider's billing dashboard is authoritative).")
    with st.expander("Evidence JSON (exactly what the AI receives)"):
        st.json(ev)

# ---------------- Dual Review
if pol["dual_review_enabled"]:
    st.divider()
    st.subheader("Dual Review (OpenAI + Anthropic)")
    missing = [LABEL[p] for p in ("openai", "anthropic") if p not in conf]
    if missing:
        st.info(f"Dual Review needs your own key for both providers. Missing: {', '.join(missing)}.")
    else:
        st.warning("This sends the same evidence to **both** providers. **Each provider bills its own request to your account.**")
        ok = st.checkbox("I understand both providers may charge me for this request")
        if st.button("Run Dual Review", disabled=not ok):
            ev = evidence(tk, tuple(tfs))
            import json as _json
            try:
                out = gw.dual_review(uid, SYSTEM.format(lang="English"),
                                     f"EVIDENCE JSON:\n{_json.dumps(ev)}\n\nQUESTION: {q or 'Explain the setups and risks.'}", authorized=ok)
                cols = st.columns(2)
                for col, (p, r) in zip(cols, out["results"].items()):
                    col.markdown(f"**{LABEL[p]}** `{r.model}`")
                    if r.ok:
                        good, bad = validate_llm_text(r.text or "", ev)
                        (col.markdown(r.text) if good else col.error(f"Rejected: numbers not in evidence {bad}"))
                    else:
                        col.error(r.message)
                st.caption(f"{out['calls']} provider calls · AMW estimate ${out['est_cost_usd'] or 0:.5f} (not an invoice)")
            except (ProviderError, AuthorizationRequired, BudgetExceeded) as e:
                st.error(str(e))

# ---------------- AI strategy drafting
st.divider()
st.subheader("Draft a Strategy Lab screener with AI")
st.caption("Bounded workflow: 1 generation call + at most your configured repair attempts, each only after the generated code "
           "fails validation. The result is saved as a DRAFT only — it is never run, published or scanned automatically.")
if not conf:
    st.info("Configure your own AI key to use drafting.")
else:
    dc = st.columns([4, 1, 1])
    desc = dc[0].text_area("Describe the screener", "Daily close above the 50 EMA, RSI between 50 and 70, volume 1.5x the 20-day average")
    lang = dc[1].selectbox("Language", ["pine", "python"])
    dprov = dc[2].selectbox("Provider", ["(policy)"] + list(conf), format_func=lambda x: LABEL.get(x, x))
    L = pol["limits"]
    n = 1 + L["max_repair_attempts"]
    auth = st.checkbox(f"Allow up to {min(n, L['max_calls_per_task'])} AI calls billed to my provider account for this task")
    if st.button("✨ Draft", disabled=not auth):
        try:
            with st.spinner("Generating and validating…"):
                r = draft_strategy(gw, uid, desc, lang, None if dprov == "(policy)" else dprov, authorized=auth)
            (st.success if r.ok else st.error)(f"{r.message} · calls {r.calls}" + (f" · est. ${r.est_cost_usd:.5f}" if r.est_cost_usd else ""))
            for e in r.errors:
                st.caption(f"• {e}")
            if r.source:
                st.code(r.source, language="python" if lang == "python" else None)
                if r.ok:
                    name = f"AI draft {datetime.datetime.now():%Y-%m-%d %H%M%S}"
                    from src.lab.contract import StrategySpec
                    sid = svc.store.lab_create(uid, name, lang, desc[:500], r.source, None, StrategySpec(language=lang).model_dump())
                    st.success(f"Saved as draft '{name}' in the Strategy Lab (id {sid}). Review it there before running.")
        except (ProviderError, AuthorizationRequired, BudgetExceeded) as e:
            st.error(str(e))

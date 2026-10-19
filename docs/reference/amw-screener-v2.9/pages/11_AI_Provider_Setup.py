import json

import pandas as pd
import streamlit as st

from src.ai.gateway import NOTICE, TASK_TYPES
from src.ai.providers import LABEL, OFFICIAL, PROVIDERS, ProviderError
from ui_common import page

svc = page("AI Provider Setup & Usage", "🔑")
uid = svc.local_user
gw = svc.ai

st.warning(NOTICE)
st.caption("Bring Your Own Key: AMW never uses a company API key for your requests and never pays for, bundles or "
           "reimburses provider charges. AI features are optional — all screening, analysis and backtesting work without a key.")

t1, t2, t3, t4 = st.tabs(["Provider keys", "Provider selection", "Spending safeguards", "Usage dashboard"])

with t1:
    conf = gw.configured(uid)
    for prov in PROVIDERS:
        with st.container(border=True):
            st.markdown(f"#### {LABEL[prov]}")
            if prov == "openai":
                st.markdown(f"1. Create an **OpenAI API** account at the [OpenAI Platform]({OFFICIAL[prov]}) and set up API billing there.\n"
                            "2. Create an API key in the platform's API keys section.\n"
                            "3. Paste it below — AMW tests it before saving.\n\n"
                            "A ChatGPT subscription (e.g. Plus) is separate and does **not** include OpenAI API credits.")
            else:
                st.markdown(f"1. Create an account in the [Anthropic Console]({OFFICIAL[prov]}) and set up API billing there.\n"
                            "2. Create an API key in the Console.\n"
                            "3. Paste it below — AMW tests it before saving.\n\n"
                            "Claude API access and billing are separate from a Claude chat subscription (Pro/Max), which does "
                            "**not** include API credits.")
            c = conf.get(prov)
            if c:
                st.success(f"Configured: `{c['masked']}` · model `{c['model'] or '-'}` · storage **{c['storage']}**")
            k = st.text_input(f"{LABEL[prov]} API key", type="password", key=f"k_{prov}",
                              help="Sent once to the AMW backend, tested, then encrypted (or kept in memory only). Never shown again.")
            cc = st.columns([2, 2, 1, 1])
            model = cc[0].text_input("Model (optional; validated against your key)", value=(c or {}).get("model") or "", key=f"m_{prov}")
            sess = cc[1].checkbox("Session only (do not store; forgotten on restart)", key=f"s_{prov}")
            if cc[2].button("Test & save", key=f"save_{prov}", type="primary", disabled=not k):
                try:
                    with st.spinner("Testing the key with the provider's model list (no token charges)…"):
                        models = gw.save_key(uid, prov, k, model or None, sess)
                    st.session_state[f"models_{prov}"] = models
                    st.session_state.pop(f"k_{prov}", None)          # drop the plaintext key from server-side widget state
                    st.success(f"Key accepted and saved ({'session only' if sess else 'encrypted'}). {len(models)} models available.")
                    st.rerun()
                except ProviderError as e:
                    st.error(f"Not saved — {e.code}: {e}")
            if cc[3].button("Delete key", key=f"del_{prov}", disabled=not c):
                gw.delete_key(uid, prov)
                st.success("Key deleted. Future AI requests to this provider are disabled until you add a key.")
                st.rerun()
            ms = st.session_state.get(f"models_{prov}")
            if c and ms and c["storage"] == "encrypted":
                sel = st.selectbox("Change model", ms, index=ms.index(c["model"]) if c.get("model") in ms else 0, key=f"ms_{prov}")
                if st.button("Use this model", key=f"um_{prov}"):
                    svc.store.ai_set_model(uid, prov, sel)
                    st.rerun()

pol = gw.policy(uid)
with t2:
    with st.form("sel"):
        modes = {"default": "Default provider (OpenAI only / Claude only / your default when both)",
                 "manual": "Manual selection for every request", "task_routing": "Task-based routing"}
        sel = st.radio("Provider selection", list(modes), format_func=modes.get, index=list(modes).index(pol["selection"]))
        dp = st.selectbox("Default provider", ["(none)", *PROVIDERS], format_func=lambda x: LABEL.get(x, x),
                          index=([None, *PROVIDERS]).index(pol["default_provider"]))
        st.markdown("**Task routing** (used when *Task-based routing* is selected)")
        routes = {}
        for t, desc in TASK_TYPES.items():
            if t in ("connection_test", "dual_review"):
                continue
            cur = (pol.get("task_routes") or {}).get(t, "(default)")
            v = st.selectbox(desc, ["(default)", *PROVIDERS], index=["(default)", *PROVIDERS].index(cur), key=f"r_{t}",
                             format_func=lambda x: LABEL.get(x, x))
            if v != "(default)":
                routes[t] = v
        fb = st.radio("If a provider fails", ["none", "ask", "auto"], index=["none", "ask", "auto"].index(pol["fallback"]),
                      format_func={"none": "Stop and show the error", "ask": "Stop and offer my other configured provider",
                                   "auto": "Automatically use my OTHER configured provider (its own charges apply)"}.get)
        dual = st.checkbox("Enable Dual Review (sends a request to BOTH providers; each bills you separately; confirmation required each time)",
                           pol["dual_review_enabled"])
        if st.form_submit_button("Save selection"):
            try:
                gw.save_policy(uid, {**pol, "selection": sel, "default_provider": None if dp == "(none)" else dp,
                                     "task_routes": routes, "fallback": fb, "dual_review_enabled": dual})
                st.success("Saved.")
            except ValueError as e:
                st.error(str(e))

with t3:
    L = pol["limits"]
    st.caption("These are AMW-side safeguards checked before every AI call. They are not billing limits on your provider "
               "account — set usage limits in your provider's own dashboard as well.")
    with st.form("lim"):
        c = st.columns(3)
        nl = dict(L)
        nl["max_calls_per_task"] = int(c[0].number_input("Max AI calls per task", 1, 50, L["max_calls_per_task"]))
        nl["max_retries"] = int(c[1].number_input("Max retries per call", 0, 5, L["max_retries"]))
        nl["max_output_tokens"] = int(c[2].number_input("Max output tokens per call", 1, 64000, L["max_output_tokens"]))
        c = st.columns(3)
        nl["max_repair_attempts"] = int(c[0].number_input("Max AI repair attempts (strategy drafting)", 0, 5, L["max_repair_attempts"]))
        nl["max_agent_depth"] = int(c[1].number_input("Max agent recursion depth", 0, 3, L["max_agent_depth"]))
        nl["confirm_calls_above"] = int(c[2].number_input("Ask for confirmation when a task may exceed N calls", 0, 50, L["confirm_calls_above"]))
        c = st.columns(3)
        nl["daily_requests"] = int(c[0].number_input("Daily request threshold", 1, 100000, L["daily_requests"]))
        nl["monthly_requests"] = int(c[1].number_input("Monthly request threshold", 1, 1000000, L["monthly_requests"]))
        c = st.columns(3)
        v = c[0].number_input("Max estimated cost per task (USD, 0 = off)", 0.0, 1000.0, float(L["max_cost_per_task_usd"] or 0), 0.01)
        nl["max_cost_per_task_usd"] = v or None
        v = c[1].number_input("Daily estimated cost threshold (USD, 0 = off)", 0.0, 10000.0, float(L["daily_cost_usd"] or 0), 0.1)
        nl["daily_cost_usd"] = v or None
        v = c[2].number_input("Monthly estimated cost threshold (USD, 0 = off)", 0.0, 100000.0, float(L["monthly_cost_usd"] or 0), 1.0)
        nl["monthly_cost_usd"] = v or None
        st.markdown("**Your model prices for AMW's estimate** (USD per 1M tokens; copy them from your provider's pricing page — "
                    "AMW does not ship price lists). Cost limits need a price for the model in use.")
        prices = st.text_area("Prices JSON", json.dumps(pol.get("prices") or {}, indent=1),
                              help='{"openai:<model>": {"input_per_m": 0.0, "output_per_m": 0.0, "cached_input_per_m": 0.0}}')
        if st.form_submit_button("Save safeguards"):
            try:
                gw.save_policy(uid, {**pol, "limits": nl, "prices": json.loads(prices or "{}")})
                st.success("Saved.")
            except (ValueError, json.JSONDecodeError) as e:
                st.error(str(e))

with t4:
    s = gw.usage_summary(uid)
    k = st.columns(3)
    for col, (lab, key) in zip(k, (("Today", "today"), ("This month", "month"), ("Last 30 days", "last_30d"))):
        col.metric(f"{lab}: provider calls", s[key]["requests"], f"est. ${s[key]['est_cost_usd']:.4f}", delta_color="off")
    st.info("**How to read this:** token counts are those **returned by the provider's API**. *Estimated cost* is **AMW's local "
            "calculation** from the prices you entered — it is not an invoice. **Your actual charges are only those shown in your "
            "provider's billing dashboard** ([OpenAI](https://platform.openai.com/) · [Anthropic](https://console.anthropic.com/)).")
    rows = svc.store.ai_usage(uid, limit=500)
    if rows:
        df = pd.DataFrame(rows).drop(columns=["user_id"], errors="ignore")
        df = df.rename(columns={"input_tokens": "input_tokens (provider)", "output_tokens": "output_tokens (provider)",
                                "cached_input_tokens": "cached_tokens (provider)", "est_cost_usd": "est_cost_usd (AMW estimate)"})
        st.dataframe(df, hide_index=True, width="stretch")
        st.caption("status: ok = completed call · error = provider/network error (may still be billed by some providers) · "
                   "blocked = stopped by AMW before calling the provider (not billed) · test = key test via the model list (no token charges). `fallback_from` marks provider fallbacks.")
    else:
        st.caption("No AI requests yet.")

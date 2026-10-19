import pandas as pd
import streamlit as st

from src.saas.entitlements import plan_limits
from ui_common import page

svc = page("Data Health & Admin", "🩺")
uid = svc.local_user
me = svc.store.get_user(uid)
t1, t2 = st.tabs(["Data-source health", "Administration"])

with t1:
    ds = svc.data_source_status(uid)
    k = st.columns(3)
    k[0].metric("Active data source", ds["active"])
    k[1].metric("Data kind", ds["data_kind"] or "-")
    k[2].metric("Selected", ds["selected"])
    if ds["fallback"]:
        st.warning(f"Fallback in use: {ds['reason']}")
    h = svc.provider(svc.cfg(uid)).health()
    st.markdown("**Default provider**")
    st.json(h)
    fs = svc.feed.status(uid)
    st.markdown("**Live feed (your FYERS account)**")
    if not fs["connected"]:
        st.info("FYERS not connected.")
    else:
        c = fs["collector"] or {}
        st.write({"session valid": fs["connection"]["token_valid"], "status": c.get("status", "not running"),
                  "runs in": c.get("runner", "-"), "last bar": c.get("last_bar"), "errors": c.get("errors")})
    last = svc.last_scan
    st.markdown("**Data quality in the last scan of this session**")
    if last is None or not getattr(last, "quality", None):
        st.info("No scan in this session yet.")
    else:
        q = pd.DataFrame(last.quality)
        bad = q[q.status != "OK"] if "status" in q else q
        st.caption(f"{len(q)} symbol/timeframe frames checked · {len(bad)} not OK")
        if len(bad):
            st.dataframe(bad, hide_index=True, width="stretch")
    st.caption("Statuses: OK, STALE (older than the allowed trading days), INSUFFICIENT (too few bars), INVALID, UNAVAILABLE. "
               "Missing data is never filled; forming candles are never evaluated.")

with t2:
    if me["role"] != "admin":
        st.info("Administration is available to admins only.")
    else:
        users = pd.DataFrame(svc.store.list_users())
        st.dataframe(users, hide_index=True, width="stretch")
        plans = list((svc.base_cfg.get("plans") or {}).keys())
        c = st.columns(3)
        who = c[0].selectbox("User", users.id.tolist(), format_func=lambda i: users.set_index("id").username[i])
        plan = c[1].selectbox("Plan", plans)
        if c[2].button("Set plan"):
            svc.store.set_plan(int(who), plan)
            svc.store.audit("plan_change", f"user {who} -> {plan} by admin {uid}", str(uid))
            st.session_state.admin_flash = f"Plan set to {plan}. (Billing-driven plan changes come from verified webhooks.)"
            st.rerun()
        if st.session_state.get("admin_flash"):
            st.success(st.session_state.pop("admin_flash"))
        from src.saas.entitlements import commercial_status
        cs = commercial_status(svc.base_cfg)
        if cs["notice"]:
            st.warning(cs["notice"] + " No prices are configured and no paid subscription is active.")
        st.markdown("**Plan limits (config/settings.yaml)**")
        st.dataframe(pd.DataFrame({p: plan_limits(p, svc.base_cfg) for p in plans}), width="stretch")
        st.markdown("**Recent audit log**")
        st.dataframe(pd.DataFrame(svc.store.audit_rows(100)), hide_index=True, width="stretch")

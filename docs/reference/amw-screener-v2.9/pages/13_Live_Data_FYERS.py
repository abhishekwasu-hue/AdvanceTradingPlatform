import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.feed.collector import MAX_SYMBOLS
from src.feed.fyers import FyersError
from ui_common import page

svc = page("Live Data (FYERS)", "📡")
DEFAULT_REDIRECT = "http://localhost:8501/Live_Data_FYERS"     # Streamlit URL of this page (pages/13_Live_Data_FYERS.py)
uid = svc.local_user
feed = svc.feed

st.caption("1-minute candles from **your own FYERS account**, collected in the background and pushed to your dashboard and "
           "API stream. Only completed candles are stored or shown. Data is licensed to your account: it is shown only to you, "
           "never to other users. Analysis only — no orders are placed.")

# ---- OAuth redirect: FYERS sends ?auth_code=...&state=... to the redirect URI (set it to this page's URL)
qp = st.query_params
if qp.get("auth_code") and qp.get("state"):
    try:
        exp = feed.complete_login(uid, qp["auth_code"], qp["state"])
        st.success(f"Logged in to FYERS. Session valid until about {pd.Timestamp(exp).tz_localize('UTC').tz_convert('Asia/Kolkata'):%d %b %H:%M} IST.")
    except FyersError as e:
        st.error(f"Login failed: {e}")
    st.query_params.clear()

stt = feed.status(uid)
t1, t2, t3 = st.tabs(["Connection", "Feed", "Live bars"])
with t1:
    conn = stt["connection"]
    if conn:
        st.success(f"FYERS app `{conn['app_id']}` connected · redirect `{conn['redirect_uri']}` · "
                   + ("session **valid**" if conn["token_valid"] else "session **expired — log in today**"))
    with st.form("fy", clear_on_submit=True):          # the secret never stays in widget state
        st.markdown("1. In the [FYERS API dashboard](https://myapi.fyers.in/dashboard/) create an app for your own account "
                    "(AMW only reads market data — it never places orders).\n"
                    f"2. Set the app's **Redirect URL** to this page, e.g. `{DEFAULT_REDIRECT}` (must match exactly).\n"
                    "3. Enter the App ID and Secret below (stored encrypted, never shown again).")
        c = st.columns(3)
        app_id = c[0].text_input("App ID", placeholder="XXXXXXXXXX-100", key="fy_app_id")
        secret = c[1].text_input("Secret", type="password", key="fy_secret")
        redirect = c[2].text_input("Redirect URL", value=(conn or {}).get("redirect_uri") or DEFAULT_REDIRECT)
        if st.form_submit_button("Save connection"):
            try:
                feed.connect(uid, app_id, secret, redirect)
                for k in ("fy_app_id", "fy_secret", "fy_url"):     # drop the plaintext from server-side widget state
                    st.session_state.pop(k, None)
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    if conn:
        st.markdown("**Daily login** — FYERS sessions expire every day; log in once each trading morning.")
        if st.button("Generate FYERS login link"):
            st.session_state.fy_url = feed.login_url(uid)
        if st.session_state.get("fy_url"):
            st.link_button("Log in to FYERS", st.session_state.fy_url)
            st.caption("After login FYERS redirects back here and the session is saved automatically. If the redirect cannot reach "
                       "this app, paste the full redirected URL below.")
            pasted = st.text_input("Redirected URL (fallback)")
            if pasted and st.button("Complete login"):
                from urllib.parse import parse_qs, urlparse
                q = parse_qs(urlparse(pasted).query)
                try:
                    feed.complete_login(uid, q.get("auth_code", [""])[0], q.get("state", [""])[0])
                    st.success("Logged in.")
                    st.rerun()
                except FyersError as e:
                    st.error(str(e))
        src = svc.store.get_pref(uid, "data_source", "default")
        new = st.radio("Data source for scans", ["default", "fyers"], index=["default", "fyers"].index(src), horizontal=True,
                       format_func={"default": "Default (Yahoo, delayed)", "fyers": "My FYERS account (needs today's login)"}.get)
        if new != src:
            svc.store.set_pref(uid, "data_source", new)
            st.rerun()
        if st.button("Disconnect FYERS (deletes stored credentials)"):
            feed.disconnect(uid)
            st.rerun()

with t2:
    saved = stt["saved"] or {}
    syms = st.text_area(f"Symbols (max {MAX_SYMBOLS}; NSE symbols or ^NSEI / ^NSEBANK)",
                        ", ".join(saved.get("symbols", ["RELIANCE.NS", "TCS.NS", "INFY.NS", "^NSEI"])))
    every = st.select_slider("Poll every N minutes (bars are always 1-minute; missed minutes are caught up from FYERS)",
                             [1, 2, 3, 5, 10, 15], value=saved.get("poll_every_min", 1))
    c = st.columns(2)
    if c[0].button("▶ Start background feed", type="primary", disabled=not stt["connected"]):
        try:
            lst = [s.strip().upper() for s in syms.replace("\n", ",").split(",") if s.strip()]
            lst = [s if s.startswith("^") or s.endswith(".NS") else s + ".NS" for s in lst]
            feed.start(uid, lst, int(every))
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    if c[1].button("⏹ Stop feed", disabled=not stt["collector"]):
        feed.stop(uid)
        st.rerun()

    @st.fragment(run_every=5)
    def status_box():
        s = feed.status(uid)["collector"]
        if not s:
            st.info("Feed not running.")
            return
        colour = {"RUNNING": st.success, "WAITING": st.info, "MARKET_CLOSED": st.info, "DEGRADED": st.warning}.get(s["status"], st.error)
        colour(f"**{s['status']}** — {s['detail']}")
        k = st.columns(5)
        for col, (label, key) in zip(k, [("Polls", "polls"), ("New bars", "bars_new"), ("Revised", "bars_revised"),
                                         ("Rejected", "rejected"), ("Errors", "errors")]):
            col.metric(label, s.get(key, 0))
        st.caption(f"Runs in: {s.get('runner', '-')} · last poll {s.get('last_poll') or '-'} UTC · last bar {s.get('last_bar') or '-'}"
                   + (f" · last error: {s['last_error']}" if s.get("last_error") else ""))
    status_box()
    st.caption("One feed per FYERS account runs across the whole app (dashboard and API share it). External clients can "
               "receive the same bars live over WebSocket: `ws://<api-host>:8000/api/v1/stream/bars` "
               "(send `{\"token\": \"<your API token>\", \"symbols\": [...]}` as the first message).")

with t3:
    @st.fragment(run_every=10)
    def bars_box():
        latest = svc.store.bars_latest(uid)
        if not latest:
            st.info("No bars stored yet.")
            return
        st.dataframe(pd.DataFrame(latest)[["symbol", "ts", "open", "high", "low", "close", "volume", "revisions", "received_at"]],
                     hide_index=True, width="stretch")
        sym = st.selectbox("Chart", [r["symbol"] for r in latest], key="live_sym")
        b = pd.DataFrame(svc.store.bars_get(uid, sym, limit=375))
        if len(b):
            fig = go.Figure(go.Candlestick(x=b.ts.str[11:16], open=b.open, high=b.high, low=b.low, close=b.close))
            fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), xaxis_rangeslider_visible=False,
                              title=f"{sym} · 1-minute · FYERS · last completed bar {b.ts.iloc[-1][:16]}")
            fig.update_xaxes(type="category", nticks=12)
            st.plotly_chart(fig, width="stretch")
    bars_box()

import os

import pandas as pd
import streamlit as st

from src.config import STRATEGIES, TIMEFRAMES
from src.data.universe import custom_universe
from ui_common import page

svc = page("Watchlist & Alerts", "🔔")
uid = svc.local_user
t1, t2, t3, t4, t5, t6 = st.tabs(["Watchlists", "Alert rules", "Delivery history", "Telegram & email", "Schedules",
                                  "Notifications"])
with t1:
    wl = svc.store.watchlists(uid)
    for w in wl:
        c = st.columns([2, 6, 1])
        c[0].markdown(f"**{w['name']}**")
        c[1].caption(", ".join(w["symbols"]))
        if c[2].button("🗑", key=f"del_{w['name']}"):
            svc.store.delete_watchlist(uid, w["name"])
            st.rerun()
    st.markdown("#### New / update")
    name = st.text_input("Name")
    syms = st.text_area("Symbols", "RELIANCE, TCS")
    u = custom_universe(syms)
    for e in u.errors:
        st.warning(e)
    if st.button("💾 Save watchlist") and name:
        svc.store.save_watchlist(uid, name, list(u.symbols.ticker))
        st.success("Saved — scan it in the Screener as 'Watchlist: <name>'")
        st.rerun()
with t2:
    st.caption("Alerts fire only on real qualified/candidate signals found during scans. Once per signal per channel; 'suppressed' during cooldown.")
    from src.services.alerts import notify_settings
    _ns = notify_settings(svc.store, uid)
    tg = bool(_ns["telegram"].get("verified"))
    em = bool(os.environ.get("SMTP_HOST") and _ns.get("email"))
    st.markdown(f"Channels: in-app ✅ · Telegram {'✅ linked' if tg else '❌ not linked (tab Telegram & email)'} · "
                f"Email {'✅' if em else '❌ (set your address; server needs SMTP)'}")
    for r in svc.store.alert_rules(uid):
        c = st.columns([3, 5, 1, 1])
        c[0].markdown(f"**{r['name']}** {'🟢' if r['enabled'] else '⚪'}")
        c[1].caption(f"strategies {r['strategies'] or 'all'} · min score {r['min_score']} · dirs {r['directions'] or 'both'} · "
                     f"tfs {r['timeframes'] or 'all'} · symbols {r['symbols'] or 'all'} · cooldown {r['cooldown_minutes']}m · {r['channels']}")
        if c[2].button("on/off", key=f"t{r['id']}"):
            svc.store.set_alert_enabled(uid, r["id"], not r["enabled"])
            st.rerun()
        if c[3].button("🗑", key=f"d{r['id']}"):
            svc.store.delete_alert_rule(uid, r["id"])
            st.rerun()
    with st.form("new_rule"):
        st.markdown("#### New rule")
        name = st.text_input("Rule name")
        c = st.columns(3)
        strats = c[0].multiselect("Strategies", STRATEGIES)
        dirs = c[1].multiselect("Direction", ["bullish", "bearish"])
        tfs = c[2].multiselect("Timeframes", TIMEFRAMES)
        c = st.columns(3)
        ms = c[0].slider("Min score", 0, 100, 55)
        cd = c[1].number_input("Cooldown (min)", 0, 10080, 120)
        ch = c[2].multiselect("Channels", ["in_app", "telegram", "email"], ["in_app"])
        sy = st.text_input("Symbols (empty = all)", "")
        if st.form_submit_button("➕ Add rule") and name:
            from src.saas.entitlements import EntitlementError, check
            try:
                check(svc.store, uid, "alert_rules", len(svc.store.alert_rules(uid)) + 1)
            except EntitlementError as e:
                st.error(str(e))
                st.stop()
            svc.store.add_alert_rule(uid, name=name, enabled=True, strategies=strats, min_score=ms, directions=dirs, timeframes=tfs,
                                     symbols=list(custom_universe(sy).symbols.ticker), cooldown_minutes=int(cd), channels=ch or ["in_app"])
            st.rerun()
with t3:
    ev = svc.store.alert_events(uid)
    st.dataframe(pd.DataFrame(ev) if ev else pd.DataFrame({"info": ["No alerts yet"]}), hide_index=True, width="stretch")

with t4:
    from src.notify.telegram import TelegramError
    from src.services import alerts as al
    ns = al.notify_settings(svc.store, uid)
    t = ns["telegram"]
    st.caption("AMW never asks for your Telegram password, phone code or login session. Linking uses the Telegram Bot API only.")
    if t.get("verified"):
        st.success(f"Telegram linked ({'official AMW bot' if t['mode'] == 'official' else 'your own bot @' + str(t.get('bot_username'))}) · "
                   f"chat {t['chat_id']}" + (f" · token {t.get('token_masked')}" if t["mode"] == "own" else ""))
        if st.button("Unlink Telegram"):
            al.unlink_telegram(svc.store, uid)
            st.rerun()
    else:
        c1, c2 = st.columns(2)
        with c1.container(border=True):
            st.markdown("**Option A — official AMW bot**")
            if not al.official_token():
                st.info("The official bot is not configured on this server (operator sets AMW_TELEGRAM_BOT_TOKEN).")
            else:
                if st.button("Get link code"):
                    st.session_state.tg_code = al.start_official_link(svc.store, uid)
                code = st.session_state.get("tg_code") or t.get("link_code")
                if code:
                    st.code(f"/start {code}")
                    st.caption("Send exactly this message to the AMW bot in Telegram, then click Verify.")
                    if st.button("Verify"):
                        try:
                            st.success(f"Linked chat {al.verify_official_link(svc.store, uid)}")
                            st.rerun()
                        except TelegramError as e:
                            st.error(str(e))
        with c2.container(border=True):
            st.markdown("**Option B — your own bot** (create with @BotFather; send it any message first)")
            tok = st.text_input("Bot token", type="password", key="tg_bot_token")
            chat = st.text_input("Your chat id")
            if st.button("Test & link", disabled=not (tok and chat)):
                try:
                    r = al.set_own_bot(svc.store, uid, tok, chat)
                    st.session_state.pop("tg_bot_token", None)            # drop the plaintext token from widget state
                    st.success(f"Linked @{r['bot']} — a test message was sent.")
                    st.rerun()
                except TelegramError as e:
                    st.error(f"{e.code}: {e}")
    with st.form("notify"):
        email = st.text_input("Alert email address (server SMTP required)", ns.get("email") or "")
        q = st.text_input("Quiet hours (exchange time, e.g. 22:00-07:00; empty = none)", "-".join(ns["quiet_hours"]) if ns.get("quiet_hours") else "")
        mph = st.number_input("Max Telegram/email alerts per hour", 1, 500, int(ns.get("max_per_hour") or 20))
        if st.form_submit_button("Save delivery settings"):
            try:
                ns.update(email=email or None, quiet_hours=q.split("-") if q else None, max_per_hour=int(mph))
                al.save_notify(svc.store, uid, ns)
                st.success("Saved.")
            except ValueError as e:
                st.error(str(e))

with t5:
    from src.services.scheduler import validate_schedule
    st.caption("Scheduled scans run in the exchange timezone on trading days (weekends and configured holidays skipped). "
               "Desktop: schedules run while the app is open (set AMW_SCHEDULER=1). Server: the API process runs them.")
    for s_ in svc.store.schedule_list(uid):
        c = st.columns([3, 5, 1])
        c[0].markdown(f"**{s_['name']}** ({s_['kind']})")
        c[1].caption(f"{'daily at ' + s_['at_time'] if s_['at_time'] else 'every ' + str(s_['interval_minutes']) + ' min in session'} · "
                     f"{s_['config'].get('universe')} · last {s_['last_run'] or '-'} {s_['last_status'] or ''} {s_['last_error'] or ''}")
        if c[2].button("🗑", key=f"sd{s_['id']}"):
            svc.store.schedule_delete(uid, s_["id"])
            st.rerun()
    with st.form("sched"):
        c = st.columns(3)
        nm = c[0].text_input("Name")
        kind = c[1].selectbox("Kind", ["scan", "scan_report", "scanner"],
                              format_func={"scan": "Scan (alerts)", "scan_report": "Scan + PDF reports to Telegram",
                                           "scanner": "My saved scanner (latest version)"}.get)
        uni = c[2].selectbox("Universe", list(svc.cfg()["universe"]["sources"]) + ["Custom"] + [f"Watchlist: {w['name']}" for w in svc.store.watchlists(uid)])
        syms = st.text_input("Custom symbols", "RELIANCE, TCS, INFY")
        c = st.columns(3)
        at = c[0].text_input("Daily at (HH:MM, exchange time)", "15:45")
        iv = c[1].number_input("…or every N minutes in session (0 = use daily time)", 0, 1440, 0)
        lim = c[2].number_input("First N symbols (0 = all)", 0, 3000, 50)
        from src.scanner.spec import is_scanner as _iss
        _mine = [x for x in svc.store.lab_list(uid) if _iss(x)]
        scn = st.selectbox("Saved scanner (for kind 'My saved scanner')", [None] + [x["id"] for x in _mine],
                           format_func=lambda i: "-" if i is None else next(x["name"] for x in _mine if x["id"] == i))
        if st.form_submit_button("Add schedule") and nm:
            try:
                _conf = {"universe": uni, "symbols": syms, "limit": int(lim) or None, "top_n": 3, "scanner_id": scn}
                validate_schedule(svc.store, uid, kind, None if iv else at, int(iv) or None, svc.cfg(), _conf)
                svc.store.schedule_add(uid, name=nm, kind=kind, config=_conf,
                                       at_time=None if iv else at, interval_minutes=int(iv) or None, trading_days_only=True, enabled=True)
                st.rerun()
            except (ValueError, PermissionError) as e:
                st.error(str(e))

with t6:
    from src.services import notify_events as _ne
    st.caption("System notifications are built from stored records (canonical signal, forward tests, feed status). "
               "In-app is always on; Telegram/e-mail are opt-in per kind and follow quiet hours and the hourly cap above. "
               "A message counts as delivered only when the channel confirmed it.")
    cur = _ne.prefs(svc.store, uid)
    labels = {"signal_qualified": "Signal later qualified (lifecycle event)", "signal_expired": "Signal expired",
              "signal_invalidated": "Signal invalidated", "signal_completed": "Signal target reached",
              "forward_closed": "Forward test closed (paper)", "daily_summary": "Daily summary (after the close)",
              "feed_failure": "Live data feed problem", "backtest_done": "Backtest finished"}
    with st.form("evprefs"):
        newp = {}
        for k in _ne.KINDS:
            newp[k] = st.multiselect(labels[k], ["in_app", "telegram", "email"], default=cur[k], key=f"ev_{k}")
        if st.form_submit_button("Save notification preferences"):
            try:
                _ne.save_prefs(svc.store, uid, {k: (v or ["in_app"]) for k, v in newp.items()})
                st.success("Saved.")
            except ValueError as e:
                st.error(str(e))
    inbox = svc.store.notifications(uid, 200)
    if inbox:
        st.dataframe(pd.DataFrame(inbox)[["created", "kind", "channel", "status", "error", "message"]], hide_index=True,
                     width="stretch")
    else:
        st.info("No notifications yet.")


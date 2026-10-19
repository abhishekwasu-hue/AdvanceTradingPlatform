import pandas as pd
import streamlit as st

from src.risk import cost_per_side, position_size
from ui_common import page
from src.services.forward import mark as _fmark
from src.services.forward import stats as _fstats

svc = page("Paper Trading & Journal", "📒")
uid = svc.local_user
cfg = svc.cfg()
st.info("All fills are **simulated**; real execution may differ. No broker orders are placed.")
eq = cfg["risk"]["account_equity"]
if not eq:
    st.warning("Account equity is not set (Settings). Quantity cannot be sized automatically; you can enter it manually.")

with st.form("trade"):
    st.markdown("#### New paper trade")
    c = st.columns(4)
    sym = c[0].text_input("Symbol", st.session_state.get("detail_ticker", "RELIANCE.NS"))
    d = c[1].selectbox("Direction", ["bullish", "bearish"])
    entry = c[2].number_input("Entry", 0.0, 1e7, 0.0)
    stop = c[3].number_input("Stop", 0.0, 1e7, 0.0)
    c = st.columns(4)
    target = c[0].number_input("Target (0 = none)", 0.0, 1e7, 0.0)
    strat = c[1].text_input("Strategy tag", "")
    when = c[2].text_input("Entry time", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"))
    qty_manual = c[3].number_input("Quantity (0 = risk-based)", 0, 10_000_000, 0)
    notes = st.text_area("Notes / plan")
    sk = st.text_input("Linked signal key (optional)")
    if st.form_submit_button("➕ Add"):
        qty, planned = int(qty_manual), abs(entry - stop) * int(qty_manual)
        if qty == 0:
            plan = position_size(entry, stop, d, cfg["risk"])
            if not plan.ok:
                st.error("; ".join(plan.reasons))
                st.stop()
            qty, planned = plan.quantity, plan.risk_amount
        if entry <= 0 or stop <= 0 or qty <= 0:
            st.error("entry/stop/quantity must be valid")
        else:
            cps = cost_per_side(cfg["costs"])
            svc.store.add_trade(uid, symbol=sym, strategy=strat, direction=d, entry_price=entry, qty=qty, stop=stop,
                                target=target or None, entry_time=when, notes=notes, planned_risk=planned,
                                brokerage=round(entry * qty * cps, 2), signal_key=sk or None, mode="paper")
            st.success(f"Paper trade added: {qty} @ {entry} · planned risk {planned:.2f}")

tr = pd.DataFrame(svc.store.trades(uid))
if tr.empty:
    st.stop()
cps = cost_per_side(cfg["costs"])
sign = tr.direction.map({"bullish": 1, "bearish": -1})
tr["exit_cost"] = (tr.exit_price.fillna(0) * tr.qty * cps).round(2)
tr["pnl"] = ((tr.exit_price - tr.entry_price) * tr.qty * sign - tr.brokerage.fillna(0) - tr.exit_cost).round(2)
tr["actual_R"] = (tr.pnl / tr.planned_risk).round(2)
op = tr[tr.status == "open"]
st.markdown("#### Open positions")
if op.empty:
    st.caption("None")
else:
    st.dataframe(op[["id", "symbol", "direction", "qty", "entry_price", "stop", "target", "entry_time", "planned_risk", "notes"]], hide_index=True)
    with st.form("close"):
        c = st.columns(5)
        tid = c[0].selectbox("Trade id", op.id)
        px = c[1].number_input("Exit price", 0.0, 1e7, 0.0)
        et = c[2].text_input("Exit time", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"))
        fr = c[3].selectbox("Rules followed?", ["yes", "no"])
        rv = c[4].text_input("Review")
        if st.form_submit_button("Close") and px > 0:
            svc.store.close_trade(uid, int(tid), px, et, fr == "yes", rv)
            st.rerun()
cl = tr[tr.status == "closed"]
st.markdown("#### Closed trades & performance")
if not cl.empty:
    k = st.columns(5)
    k[0].metric("Trades", len(cl))
    k[1].metric("Net P&L", f"{cl.pnl.sum():,.2f}")
    k[2].metric("Win rate", f"{(cl.pnl > 0).mean() * 100:.0f}%")
    k[3].metric("Avg actual R", f"{cl.actual_R.mean():.2f}")
    k[4].metric("Rule adherence", f"{cl.followed_rules.mean() * 100:.0f}%" if cl.followed_rules.notna().any() else "NA")
    st.dataframe(cl[["id", "symbol", "strategy", "direction", "qty", "entry_price", "exit_price", "planned_risk", "pnl", "actual_R",
                     "followed_rules", "entry_time", "exit_time", "review"]], hide_index=True, width="stretch")
    cl = cl.assign(day=cl.exit_time.str[:10])
    c = st.columns(2)
    c[0].markdown("**Daily**")
    c[0].dataframe(cl.groupby("day").pnl.agg(["count", "sum"]), width="stretch")
    c[1].markdown("**By strategy**")
    c[1].dataframe(cl.groupby(cl.strategy.replace("", "untagged")).agg(trades=("pnl", "count"), pnl=("pnl", "sum"), avg_R=("actual_R", "mean")), width="stretch")

st.markdown("#### Automatic forward tests (paper simulation of every qualified signal)")
st.caption("Entry at the open of the bar after the signal bar; exit at stop/target (stop assumed if both are touched in one "
           "bar) or after a fixed number of bars. No orders, costs or slippage. Marked daily after the close and on demand. "
           "Fewer than 30 closed tests = INSUFFICIENT EVIDENCE.")
if st.button("🔄 Update forward tests now"):
    with st.spinner("Checking completed bars after each signal…"):
        st.success(str(_fmark(svc, uid)))
fs = _fstats(svc.store, uid)
if fs:
    st.dataframe(pd.DataFrame(fs), hide_index=True, width="stretch")
    st.dataframe(pd.DataFrame(svc.store.forward_list(uid, limit=500)).drop(columns=["user_id"], errors="ignore"),
                 hide_index=True, width="stretch")
else:
    st.info("No forward tests yet — they are created automatically when a scan stores a qualified signal.")


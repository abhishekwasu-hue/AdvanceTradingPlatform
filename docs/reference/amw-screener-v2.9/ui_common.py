"""Shared Streamlit helpers: services singleton, theme, sidebar status, common widgets."""
from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from src.config import ROOT
from src.services.app_services import Services

logging.basicConfig(filename=ROOT / "amw_screener.log", level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DISCLAIMER = ("Technical analysis only · no live orders · Score = rule-based confluence, not a probability of success · "
              "Data: Yahoo Finance (delayed/historical, not real-time)")


@st.cache_resource(show_spinner=False)
def services() -> Services:
    return Services()


def page(title: str, icon: str = "📈") -> Services:
    st.set_page_config(page_title=f"AMW AI · {title}", page_icon=icon, layout="wide")
    svc = services()
    uid = svc.local_user
    dark = svc.store.get_pref(uid, "dark", False)
    if dark:
        st.markdown("""<style>
        .stApp{background:#0e1117;color:#e6e6e6} [data-testid=stSidebar]{background:#161a23}
        [data-testid=stMetricValue]{color:#e6e6e6}</style>""", unsafe_allow_html=True)
    st.markdown("""<style>
      [data-testid=stMetric]{border:1px solid rgba(128,128,128,.25);border-radius:8px;padding:8px 12px}
      .amw-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}
      .ok{background:#e6f4ea;color:#1e7e34}.warn{background:#fff4e5;color:#a35b00}.bad{background:#fdecea;color:#b71c1c}
      button:focus-visible, a:focus-visible, input:focus-visible {outline:3px solid #1565c0 !important; outline-offset:2px}
    </style>""", unsafe_allow_html=True)
    st.title(f"{icon} {title}")
    st.caption(DISCLAIMER)
    with st.sidebar:
        st.markdown("### AMW AI Custom Stock Screener™")
        new_dark = st.toggle("🌙 Dark mode", value=bool(dark), key="dark_toggle")
        if new_dark != bool(dark):
            svc.store.set_pref(uid, "dark", new_dark)
            st.rerun()
        act = svc.jobs.active()
        if act:
            j = act[0]
            st.info(f"Scan running: {j.progress * 100:.0f}% · {j.message}")
        last = svc.last_scan
        if last:
            st.caption(f"Last scan: {last.universe} · {last.symbols_done}/{last.total} · {last.finished or 'running'}"
                       + (" · ⚠️ partial" if last.stopped_early else ""))
    return svc


def badge(status: str) -> str:
    cls = "ok" if status in ("OK", "qualified", "done", "sent") else "warn" if status in ("STALE", "candidate", "running", "suppressed") else "bad"
    return f'<span class="amw-badge {cls}">{status}</span>'


def last_scan_or_info(svc: Services):
    if svc.last_scan is None:
        st.info("No scan yet. Start a scan from the **Advanced Screener** page.")
        return None
    return svc.last_scan


def num(v, fmt="{:.2f}") -> str:
    return "NA" if v is None or (isinstance(v, float) and pd.isna(v)) else fmt.format(v)

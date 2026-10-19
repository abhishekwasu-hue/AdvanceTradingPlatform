"""UI smoke tests: every Streamlit page renders without exceptions using the SIMULATED fixture provider
(AMW_PROVIDER=fixture) and an isolated database. Key interactions are exercised. No network."""
import pathlib

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGES = ["Home.py"] + sorted(str(p.relative_to(ROOT)) for p in (ROOT / "pages").glob("*.py"))


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("AMW_PROVIDER", "fixture")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/ui.db")
    monkeypatch.delenv("AMW_LAB_LANGUAGES", raising=False)
    from src.ai.crypto import generate_master_key
    monkeypatch.setenv("AMW_SECRET_KEY", generate_master_key())
    import ui_common
    ui_common.services.clear()


def at(page):
    a = st_testing.AppTest.from_file(str(ROOT / page), default_timeout=120)
    a.run()
    return a


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    a = at(page)
    assert not a.exception, [e.value for e in a.exception]


def test_screener_scan_flow():
    a = at("pages/1_Advanced_Screener.py")
    uni = [s for s in a.selectbox if s.label == "Universe"][0]
    uni.set_value("Custom").run()
    [b for b in a.button if "Start scan" in b.label][0].click().run()
    import time
    import ui_common
    svc = ui_common.services()
    for _ in range(200):
        if svc.last_scan is not None and not svc.jobs.active():
            break
        time.sleep(0.1)
    a.run()
    assert not a.exception, [e.value for e in a.exception]
    assert svc.last_scan.symbols_done == 5
    assert any("Results:" in m.value for m in a.markdown)


def test_strategy_lab_create_pine_and_run():
    a = at("pages/10_Strategy_Lab.py")
    modes = [s for s in a.selectbox if s.label == "Authoring mode"][0]
    assert len(modes.options) == 8 and "Visual No-Code Builder" in modes.options
    [t for t in a.text_input if t.label == "Name"][0].set_value("My Pine")
    modes.set_value("pine").run()
    tpl = [s for s in a.selectbox if s.label == "Start from template"][0]
    assert "EMA trend (v5)" in tpl.options                               # template list follows the selected mode
    tpl.set_value("EMA trend (v5)").run()
    [b for b in a.button if b.label == "Create draft"][0].click().run()
    assert not a.exception, [e.value for e in a.exception]
    [b for b in a.button if "Validate" in b.label][0].click().run()
    assert any("Valid" in s.value for s in a.success), [e.value for e in a.error]
    [b for b in a.button if "Publish" in b.label][0].click().run()
    assert any("Published immutable v1" in s.value for s in a.success)


def test_strategy_lab_visual_builder_flow():
    a = at("pages/10_Strategy_Lab.py")
    [t for t in a.text_input if t.label == "Name"][0].set_value("My visual")
    [s for s in a.selectbox if s.label == "Authoring mode"][0].set_value("visual").run()
    [s for s in a.selectbox if s.label == "Start from template"][0].set_value("Bullish trend").run()
    [b for b in a.button if b.label == "Create draft"][0].click().run()
    assert not a.exception, [e.value for e in a.exception]
    assert any("LONG when" in c.value for c in a.code)                       # readable generated logic
    n_rules = sum(1 for c in a.code if c.value.lstrip().startswith("•"))
    [b for b in a.button if b.label == "🗑"][0].click().run()                 # delete a condition
    assert sum(1 for c in a.code if c.value.lstrip().startswith("•")) == n_rules - 1
    [b for b in a.button if "Validate" in b.label][0].click().run()
    assert any("Valid" in s.value for s in a.success), [e.value for e in a.error]
    [b for b in a.button if "Publish" in b.label][0].click().run()
    assert any("Published immutable v1" in s.value for s in a.success)


def test_ai_setup_page_shows_billing_notice_and_no_key_state():
    a = at("pages/11_AI_Provider_Setup.py")
    assert not a.exception
    assert any("billed separately by your selected AI provider" in w.value for w in a.warning)
    assert any("platform.openai.com" in m.value for m in a.markdown) and any("console.anthropic.com" in m.value for m in a.markdown)
    assert all(t.proto.type == t.proto.PASSWORD for t in a.text_input if "API key" in t.label)   # masked input


def test_ai_analyst_without_key_is_deterministic():
    a = at("pages/4_AI_Chart_Analyst.py")
    assert any("No AI provider key configured" in i.value for i in a.info)
    toggle = [t for t in a.toggle if t.label == "AI explanation"][0]
    assert toggle.disabled
    [b for b in a.button if "Analyse" in b.label][0].click().run()
    assert not a.exception, [e.value for e in a.exception]
    assert any("evidence-based analysis" in m.value for m in a.markdown)


def test_live_data_page_connect_and_login_link_without_leaking_secret():
    secret = "FAKEFAKE-UI-0123456789"
    a = at("pages/13_Live_Data_FYERS.py")
    assert not a.exception
    assert [b for b in a.button if "Start background feed" in b.label][0].disabled      # not connected yet
    ti = {t.label: t for t in a.text_input}
    assert ti["Secret"].proto.type == ti["Secret"].proto.PASSWORD
    assert ti["Redirect URL"].value == "http://localhost:8501/Live_Data_FYERS"
    ti["App ID"].input("ABCD1234XY-100")
    ti["Secret"].input(secret)
    [b for b in a.button if b.label == "Save connection"][0].click().run()
    assert not a.exception, [e.value for e in a.exception]
    assert any("connected" in s.value for s in a.success)
    rendered = " ".join(str(e.value) for e in list(a.markdown) + list(a.success) + list(a.caption) + list(a.text_input)
                        if getattr(e, "value", None) is not None)
    assert secret not in rendered and "ABCD1234XY-100" not in rendered               # App ID masked, secret never echoed
    [b for b in a.button if "Generate FYERS login link" in b.label][0].click().run()
    assert a.session_state["fy_url"].startswith("https://api-t1.fyers.in/api/v3/generate-authcode?")
    assert "state=" in a.session_state["fy_url"] and secret not in a.session_state["fy_url"]


def test_scanner_builder_template_save_and_nl_parse():
    a = at("pages/14_Scanner_Builder.py")
    assert not a.exception, [e.value for e in a.exception]
    assert any("NOT VALIDATED" in w.value for w in a.warning)
    [b for b in a.button if "Save as scanner" in b.label][0].click().run()
    assert not a.exception and any("Saved" in s.value for s in a.success)
    [b for b in a.button if b.label == "Parse"][0].click().run()
    assert not a.exception and any("Specification is valid" in s.value for s in a.success)


def test_data_health_admin_and_notifications_tab():
    a = at("pages/15_Data_Health_Admin.py")
    assert not a.exception, [e.value for e in a.exception]
    assert any(m.label == "Active data source" for m in a.metric)
    [b for b in a.button if b.label == "Set plan"][0].click().run()
    assert not a.exception and any("Plan set" in s.value for s in a.success)
    w = at("pages/7_Watchlist_Alerts.py")
    [b for b in w.button if "Save notification preferences" in b.label][0].click().run()
    assert not w.exception and any(s.value == "Saved." for s in w.success)

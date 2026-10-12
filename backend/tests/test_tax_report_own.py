"""Fix (BT1b): the tax report, its CSV and the list of years read only the caller's own trades - member A of an
organisation never sees member B's trades, the owner sees only their own, and nothing needs a flag. TX-1 follow-up: the
statute cited depends on the financial year - the Income-tax Act 1961 sections up to FY 2025-26, the Income-tax Act 2025
from FY 2026-27 - and every cited section carries an official source or a `verify` note."""
import uuid
from datetime import datetime, timezone

from app.ai.wording import banned_terms
from app.market_data.calendar import IST
from app.tax import report as tax
from tests.test_auth_api import client
from tests.test_phase_p_closure import _seed
from tests.test_trading_worker import _tenant
from tests.test_z3_members import member


def _org() -> dict:
    return _tenant(f"tax-own-{uuid.uuid4().hex[:8]}@example.com", with_credentials=False)    # a plan with room for members


def _at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 11, 0, tzinfo=IST).astimezone(timezone.utc)


def test_member_a_never_sees_member_b_trades_in_report_csv_or_years():
    owner = _org()
    a, b = member(owner), member(owner)
    assert a["tenant_id"] == b["tenant_id"] == owner["tenant_id"]
    # A: one FY 2026-27 equity trade. B: a crypto trade in FY 2026-27 and one in FY 2024-25 (a year A never traded).
    _seed(a["tenant_id"], a["user_id"], symbol="TCS", entry=100.0, exit_=101.0, qty=10, pnl=10.0, exit_at=_at(2026, 6, 15))
    _seed(b["tenant_id"], b["user_id"], symbol="BTCINR", exchange="CRYPTO", entry=100.0, exit_=150.0, qty=1, pnl=50.0, exit_at=_at(2026, 6, 15))
    _seed(b["tenant_id"], b["user_id"], symbol="WIPROB", entry=100.0, exit_=99.0, qty=10, pnl=-10.0, exit_at=_at(2024, 6, 14))
    # The owner's own trade, in the same year: neither member sees it, and the owner does not see theirs.
    _seed(owner["tenant_id"], owner["user_id"], symbol="OWNERCO", entry=100.0, exit_=102.0, qty=10, pnl=20.0, exit_at=_at(2026, 6, 15))

    mine = client.get("/api/tax/report?fy=2026-27&mode=ALL", headers=a["headers"]).json()
    assert mine["trades"] == 1 and mine["net_pnl"] == 10.0 and mine["scope"] == "your own trades"
    assert mine["classes"]["CRYPTO"]["trades"] == 0
    assert [r["symbol"] for r in mine["classes"]["EQUITY_INTRADAY"]["top_symbols"]] == ["TCS"]
    csv_a = client.get("/api/tax/report.csv?fy=2026-27&mode=ALL", headers=a["headers"]).text
    assert "TCS" in csv_a and "BTCINR" not in csv_a and "OWNERCO" not in csv_a
    years_a = client.get("/api/tax/years", headers=a["headers"]).json()["years"]
    assert "2026-27" in years_a and "2024-25" not in years_a

    theirs = client.get("/api/tax/report?fy=2026-27&mode=ALL", headers=b["headers"]).json()
    assert theirs["trades"] == 1 and theirs["classes"]["CRYPTO"]["trades"] == 1 and theirs["classes"]["EQUITY_INTRADAY"]["trades"] == 0
    csv_b = client.get("/api/tax/report.csv?fy=2026-27&mode=ALL", headers=b["headers"]).text
    assert "BTCINR" in csv_b and "TCS" not in csv_b and "OWNERCO" not in csv_b
    assert "2024-25" in client.get("/api/tax/years", headers=b["headers"]).json()["years"]
    old_b = client.get("/api/tax/report?fy=2024-25&mode=ALL", headers=b["headers"]).json()
    assert old_b["trades"] == 1
    assert client.get("/api/tax/report?fy=2024-25&mode=ALL", headers=a["headers"]).json()["trades"] == 0

    boss = client.get("/api/tax/report?fy=2026-27&mode=ALL", headers=owner["headers"]).json()
    assert boss["trades"] == 1 and [r["symbol"] for r in boss["classes"]["EQUITY_INTRADAY"]["top_symbols"]] == ["OWNERCO"]
    assert "2024-25" not in client.get("/api/tax/years", headers=owner["headers"]).json()["years"]


def test_statute_follows_the_financial_year():
    assert tax.statute("2025-26")["key"] == "1961" and tax.statute("2024-25")["key"] == "1961"
    assert tax.statute("2026-27")["key"] == "2025" and tax.statute("2030-31")["key"] == "2025"
    for fy in ("2025-26", "2026-27"):
        law = tax.statute(fy)
        for key in ("vda_tax", "vda_tds", "tax_audit"):
            cited = law[key]
            # Every cited section has an official source or says it is to be verified - never neither.
            assert cited["url"].startswith("https://www.incometaxindia.gov.in/") or cited["verify"], (fy, key)
        assert law["act_2025_source"]["url"].startswith("https://www.incometaxindia.gov.in/")


def test_disclaimer_cites_1961_sections_up_to_fy_2025_26_and_the_2025_act_after():
    old, new = tax.disclaimer("2025-26"), tax.disclaimer("2026-27")
    assert "115BBH" in old and "194S" in old and "Income-tax Act, 1961" in old
    assert "115BBH" not in new and "194S" not in new and "Income-tax Act, 2025" in new and "s.393(1)" in new
    assert "to be verified" in new
    for text in (old, new):
        assert text.startswith("This report is not tax advice.") and banned_terms(text) == []
        assert "chartered accountant before" not in text


def test_report_notes_and_csv_carry_the_years_statute():
    old = tax.build_report([], "2025-26", mode="LIVE")
    new = tax.build_report([], "2026-27", mode="LIVE")
    assert old["classes"]["CRYPTO"]["income_head"] == "Income from transfer of VDA (s.115BBH, Income-tax Act, 1961)"
    assert "section to be verified" in new["classes"]["CRYPTO"]["income_head"]
    assert any("s.44AB of the Income-tax Act, 1961" in n for n in old["notes"])
    assert any("tax-audit provision (section and threshold to be verified" in n for n in new["notes"])
    for body in (old, new):
        assert all(banned_terms(n) == [] for n in body["notes"]) and not any("should confirm" in n for n in body["notes"])
    csv_new = tax.to_csv(new)
    assert 'source,"Income-tax Act, 2025","s.393(1) (Table, Sl. 8(vi))",https://www.incometaxindia.gov.in/w/section-393-5' in csv_new
    assert "verify: The section of the Income-tax Act 2025 that carries the 30% rate" in csv_new
    assert "https://www.incometaxindia.gov.in/w/section-115bbh-1" in tax.to_csv(old)

"""SB-UX SU1 / SU4 (owner order ATP_ORDER_CP_SB_UX): the stepped builder's plain-words box uses ONE parser (the Copilot's
`sb.build`) through POST /api/custom-strategies/draft, and the expiry rule "next weekly on expiry day" (ROLL)."""
from datetime import date

from app.core.enums import ExpiryRule
from app.instruments.contracts import expiry_words, select_expiry
from tests.test_auth_api import _register, client

THU = date(2026, 10, 29)
NEXT_THU = date(2026, 11, 5)
LIST = [THU, NEXT_THU, date(2026, 11, 26)]


def test_roll_is_the_nearest_on_other_days():
    assert select_expiry(LIST, ExpiryRule.ROLL, date(2026, 10, 27)) == THU
    assert select_expiry(LIST, ExpiryRule.ROLL, date(2026, 10, 30)) == NEXT_THU


def test_roll_takes_the_next_expiry_on_expiry_day():
    assert select_expiry(LIST, ExpiryRule.ROLL, THU) == NEXT_THU
    # the nearest rule itself is unchanged: on expiry day it is that day's expiry
    assert select_expiry(LIST, ExpiryRule.NEAREST, THU) == THU


def test_roll_with_nothing_after_keeps_the_last_listed():
    assert select_expiry([THU], ExpiryRule.ROLL, THU) == THU
    assert select_expiry([], ExpiryRule.ROLL, THU) is None


def test_roll_fits_the_stored_column_and_reads_in_words():
    assert len(ExpiryRule.ROLL.value) <= 10          # deployments.expiry_rule is String(10): no migration
    assert expiry_words(ExpiryRule.ROLL) == "nearest (the next on expiry day)"
    assert expiry_words(ExpiryRule.NEXT) == "next"


def test_draft_needs_a_login():
    assert client.post("/api/custom-strategies/draft", json={"text": "buy when RSI 14 crosses above 60"}).status_code in (401, 403)


def test_draft_is_the_copilot_parser_and_fills_no_risk_value():
    headers = {"Authorization": f"Bearer {_register('sbflow@example.com')}"}
    res = client.post("/api/custom-strategies/draft", headers=headers,
                      json={"text": "On the 15-minute chart of NIFTY go long when RSI 14 crosses above 60"})
    assert res.status_code == 200, res.text
    d = res.json()
    assert d["mode"] == "PAPER"
    assert d["symbol"] == "NIFTY"
    assert d["timeframe"] == "15min"
    assert d["rules"] and d["rules"][0]["side"] == "long"
    assert d["rules"][0]["condition"]["left"]["indicator"] == "RSI"
    # DEF-0: nothing the words did not state is filled in
    assert d["settings"] == {"stop_loss_atr_mult": None, "atr_period": None, "target_rr": None, "min_rr": None}
    assert d["min_rr_source"] is None


def test_draft_lists_what_it_cannot_build():
    headers = {"Authorization": f"Bearer {_register('sbflow2@example.com')}"}
    d = client.post("/api/custom-strategies/draft", headers=headers, json={"text": "buy a pullback into a demand zone"}).json()
    assert d["rules"] == []
    assert any("area" in u for u in d["unsupported"])


def test_draft_refuses_an_empty_text():
    headers = {"Authorization": f"Bearer {_register('sbflow3@example.com')}"}
    assert client.post("/api/custom-strategies/draft", headers=headers, json={"text": ""}).status_code == 422

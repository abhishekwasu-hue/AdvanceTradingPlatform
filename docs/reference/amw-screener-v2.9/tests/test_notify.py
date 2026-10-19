"""Telegram tests with a MOCKED Bot API (no network): official-bot linking, user-owned bot with encrypted token,
canonical message content, bounded retries, failure classification, token redaction, unlinking, tenant isolation."""
import pytest

from src.ai import crypto
from src.notify.telegram import TelegramClient, TelegramError
from src.services import alerts
from src.storage.db import Store

OFFICIAL = "1234567:AAOFFICIALtokenOFFICIALtoken000000001"
OWN = "7654321:AAUSERownBOTtokenUSERownBOT00000002"


class MockTG:
    def __init__(self):
        self.calls, self.fail, self.updates = [], [], []

    def __call__(self, method, url, body, files, timeout):
        token, meth = url.split("/bot", 1)[1].split("/", 1)
        self.calls.append({"token": token, "method": meth, "body": body, "files": files})
        if token not in (OFFICIAL, OWN):
            return 401, {"ok": False, "description": f"Unauthorized {token}"}
        if self.fail:
            st = self.fail.pop(0)
            return st, {"ok": False, "description": f"error {st} for bot{token}"}
        if meth == "getMe":
            return 200, {"ok": True, "result": {"username": "amw_test_bot"}}
        if meth == "getUpdates":
            return 200, {"ok": True, "result": self.updates}
        return 200, {"ok": True, "result": {"message_id": len(self.calls)}}

    def sent(self):
        return [c for c in self.calls if c["method"] == "sendMessage"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AMW_SECRET_KEY", crypto.generate_master_key())
    monkeypatch.setenv("AMW_TELEGRAM_BOT_TOKEN", OFFICIAL)
    s = Store(f"sqlite:///{tmp_path}/n.db")
    a, _ = s.create_user("a")
    b, _ = s.create_user("b")
    return s, MockTG(), a, b


def test_official_bot_linking_by_code(env):
    s, tg, a, b = env
    code = alerts.start_official_link(s, a)
    with pytest.raises(TelegramError) as e:
        alerts.verify_official_link(s, a, tg)
    assert e.value.code == "not_found"
    tg.updates = [{"message": {"text": f"/start {code}", "chat": {"id": 555}}}, {"message": {"text": "/start AMW-OTHER", "chat": {"id": 999}}}]
    assert alerts.verify_official_link(s, a, tg) == 555
    st, err = alerts.send_telegram(s, a, "hello", tg)
    assert st == "sent" and tg.sent()[-1]["body"]["chat_id"] == 555 and tg.sent()[-1]["token"] == OFFICIAL
    assert alerts.send_telegram(s, b, "hello", tg)[0] == "skipped"          # b never linked -> no message anywhere


def test_own_bot_token_encrypted_and_never_exposed(env):
    s, tg, a, b = env
    with pytest.raises(TelegramError):
        alerts.set_own_bot(s, a, "not-a-token", "42", tg)
    alerts.set_own_bot(s, a, OWN, "42", tg)
    raw = s.get_pref(a, "notify")
    assert OWN not in str(raw) and OWN.split(":")[1] not in str(raw)
    pub = alerts.public_notify(alerts.notify_settings(s, a))
    assert "token_ct" not in pub["telegram"] and pub["telegram"]["token_masked"] == f"…{OWN[-4:]}"
    assert alerts.send_telegram(s, a, "x", tg)[0] == "sent" and tg.sent()[-1]["token"] == OWN
    assert alerts.send_telegram(s, b, "x", tg)[0] == "skipped"
    assert "Telegram" not in repr(TelegramClient(OWN, tg)) or OWN not in repr(TelegramClient(OWN, tg))


def test_bounded_retries_and_classification(env):
    s, tg, a, b = env
    alerts.set_own_bot(s, a, OWN, "42", tg)
    n0 = len(tg.sent())
    tg.fail = [429, 503, 502, 500]
    st, err = alerts.send_telegram(s, a, "x", tg, sleeper=lambda t: None)
    assert st == "failed" and len(tg.sent()) - n0 == alerts.MAX_RETRIES + 1
    assert OWN not in err and OWN.split(":")[1] not in err
    tg.fail = [403]
    st, err = alerts.send_telegram(s, a, "x", tg, sleeper=lambda t: None)
    assert st == "failed" and err.startswith("blocked")
    tg.fail = [503]
    assert alerts.send_telegram(s, a, "x", tg, sleeper=lambda t: None)[0] == "sent"


def test_unlink_disables_delivery(env):
    s, tg, a, b = env
    alerts.set_own_bot(s, a, OWN, "42", tg)
    alerts.unlink_telegram(s, a)
    n = len(tg.calls)
    assert alerts.send_telegram(s, a, "x", tg)[0] == "skipped" and len(tg.calls) == n


def test_message_comes_from_canonical_record(env):
    s, tg, a, b = env
    alerts.set_own_bot(s, a, OWN, "42", tg)
    row = {"key": "k1", "ticker": "A.NS", "symbol": "A", "strategy": "breakout", "strategy_version": 1, "setup": "Breakout (x)",
           "timeframe": "1D", "direction": "bullish", "signal_time": "2026-10-16 00:00", "data_time": "2026-10-16 00:00",
           "status": "qualified", "label": "Strong Candidate", "score": 77.0, "rr": 2.1, "stop": 95.5, "target": 120.0,
           "provider": "fixture", "data_kind": "simulated", "data_status": "OK"}
    s.record_signals([row], a)
    s.add_alert_rule(a, name="r", enabled=True, strategies=[], min_score=0, directions=[], timeframes=[], symbols=[],
                     cooldown_minutes=0, channels=["telegram"])
    out = alerts.process_alerts(s, [{**row, "score": 1.0}], a, alerts.default_senders(s, tg))   # scan row differs; message uses stored record
    assert out and out[0]["status"] == "sent"
    text = tg.sent()[-1]["body"]["text"]
    assert "Score 77.0" in text and "Stop 95.5" in text and s.scoped_key(a, "k1")[:8] in text and "simulated" in text

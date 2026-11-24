"""Owner 24 Nov ("he sarv info dashbord warun set keli aahech"): the SRV2 bot's Telegram ✅ cards and Vision come from what the
owner set in ATP's dashboard (the organisation's Telegram bot + his linked chat; his Anthropic key and the model he picked);
a value .env set wins; nothing is logged but names; a change restarts the bot's services."""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.srv2bot import dashboard_env as D
from app.srv2bot import job


def _clean(monkeypatch, from_env=frozenset()):
    for n in D.NAMES:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(D, "FROM_ENV", frozenset(from_env))


def test_the_dashboard_values_are_put_in_the_environment_and_dot_env_wins(monkeypatch):
    _clean(monkeypatch, {"TELEGRAM_CHAT_ID"})
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "from-env")
    found = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c", "TELEGRAM_APPROVER_IDS": "c", "VISION_CALLBACK_SECRET": "s",
             "ANTHROPIC_API_KEY": "k", "VISION_SIGNAL_MODEL": "m"}
    names = D.apply(found)
    assert os.environ["TELEGRAM_CHAT_ID"] == "from-env"                 # .env wins
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "t" and os.environ["ANTHROPIC_API_KEY"] == "k"
    assert set(names) == set(D.NAMES)
    names = D.apply({"TELEGRAM_BOT_TOKEN": "t"})                          # he removed his key / unlinked: gone again
    assert "ANTHROPIC_API_KEY" not in os.environ and names == ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def test_values_take_the_org_bot_with_his_linked_chat_and_his_anthropic_key(monkeypatch):
    from app.ai import byok
    from app.screener import evidence_store as E

    async def bot(session, tid):
        return "org-token"

    async def chat(session, tid, uid):
        return "42"

    async def key(session, user, provider):
        assert provider == "anthropic"
        return SimpleNamespace(model="claude-x")
    monkeypatch.setattr(E, "_bot", bot)
    monkeypatch.setattr(E, "own_chat", chat)
    monkeypatch.setattr(byok, "get_key", key)
    monkeypatch.setattr(byok, "open_key", lambda record, user: "sk-test")
    monkeypatch.setattr(byok, "effective_model", lambda record, provider: record.model)
    owner = SimpleNamespace(tenant_id=1, user=SimpleNamespace(id=7))
    got = asyncio.run(D.values(object(), owner))
    assert got["TELEGRAM_BOT_TOKEN"] == "org-token" and got["TELEGRAM_CHAT_ID"] == got["TELEGRAM_APPROVER_IDS"] == "42"
    assert len(got["VISION_CALLBACK_SECRET"]) == 64
    assert got["ANTHROPIC_API_KEY"] == "sk-test" and got["VISION_SIGNAL_MODEL"] == "claude-x"

    async def no_chat(session, tid, uid):
        return None                                                     # linked only via the platform bot: no buttons

    async def no_key(session, user, provider):
        return None
    monkeypatch.setattr(E, "own_chat", no_chat)
    monkeypatch.setattr(byok, "get_key", no_key)
    assert asyncio.run(D.values(object(), owner)) == {}


def test_the_worker_reads_them_every_refresh_and_restarts_the_services_on_a_change(monkeypatch):
    _clean(monkeypatch)
    seen = {"stop": 0, "read": 0, "vals": {"TELEGRAM_BOT_TOKEN": "t"}}

    async def values(session, who):
        seen["read"] += 1
        return dict(seen["vals"])
    monkeypatch.setattr(D, "values", values)
    monkeypatch.setattr(job, "stop_services", lambda: seen.__setitem__("stop", seen["stop"] + 1))
    monkeypatch.setattr(job, "_settings", {"at": None, "names": None})
    t0 = datetime(2026, 11, 24, 10, tzinfo=UTC)
    assert asyncio.run(job.refresh_settings(None, None, t0)) is True and seen["stop"] == 0     # first read: nothing to restart
    assert asyncio.run(job.refresh_settings(None, None, t0 + timedelta(seconds=10))) is False  # not again within REFRESH_SECONDS
    later = t0 + timedelta(seconds=job.REFRESH_SECONDS + 1)
    assert asyncio.run(job.refresh_settings(None, None, later)) is True and seen["stop"] == 0  # same names: no restart
    seen["vals"]["ANTHROPIC_API_KEY"] = "k"
    assert asyncio.run(job.refresh_settings(None, None, later + timedelta(seconds=job.REFRESH_SECONDS + 1))) is True
    assert seen["stop"] == 1 and seen["read"] == 3                      # he added his key: the services restart with it

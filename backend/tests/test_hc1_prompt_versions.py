"""H-C1 f: every LLM call carries a prompt version (a prompt edit changes it), and monitor.py says what it does."""
import asyncio
import pathlib
import re

from sqlalchemy import select

from app.ai import copilot, knowledge, metering, prompt_versions, strategist, thesis
from app.ai.providers import Completion
from app.db.models import LlmCallRecord
from app.scanner import ai as scanner_ai
from tests.test_auth_api import _session_factory
from tests.test_phase_l_ai import _owner

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _run(coro):
    return asyncio.run(coro)


def test_a_prompt_edit_changes_its_version():
    v = prompt_versions.version_of("copilot", "Answer from the facts.")
    assert v == prompt_versions.version_of("copilot", "Answer from the facts.") and v.startswith("copilot-")
    assert v != prompt_versions.version_of("copilot", "Answer from the facts!")
    versions = {copilot.PROMPT_VERSION, knowledge.PROMPT_VERSION, thesis.PROMPT_VERSION, strategist.PROMPT_VERSION}
    assert len(versions) == 4 and all(v for v in versions) and scanner_ai.PROMPT_VERSION


def test_every_module_that_calls_a_model_sets_a_version():
    callers = [p for p in APP.rglob("*.py") if ".complete(" in p.read_text(encoding="utf-8") and p.name != "providers.py"
               and "def complete(" not in p.read_text(encoding="utf-8")]
    assert callers, "no LLM callers found - the scan is broken"
    missing = [str(p.relative_to(APP)) for p in callers
               if not re.search(r"stamp\(|\.prompt_version\s*=", p.read_text(encoding="utf-8"))]
    assert missing == []


class _Versioned:
    name, model = "anthropic", "test-model"

    def __init__(self, answer):
        self.answer, self.prompt_version, self.seen = answer, None, []

    async def complete(self, system, user, *, max_tokens=2000):
        self.seen.append(self.prompt_version)
        return self.answer


def test_the_narrators_stamp_their_version_before_calling():
    p = _Versioned("NIFTY 50 is at 25200.")
    _run(copilot.narrate(p, "en", "market", "how is nifty?", ["NIFTY 50 last 25200"]))
    assert p.seen == [copilot.PROMPT_VERSION]
    g = _Versioned("RSI measures momentum.")
    _run(knowledge.ai_answer(g, "what is rsi", "en", None, None))
    assert g.seen == [knowledge.PROMPT_VERSION]


def test_an_unstamped_call_is_logged_as_unversioned_never_null():
    _, me = _owner("hc1f-unversioned@example.com")

    class _Inner:
        name, model = "anthropic", "m"

        async def complete_full(self, system, user, *, max_tokens=2000):
            return Completion(text="ok", model="m", provider="anthropic")

    async def go():
        async with _session_factory() as session:
            provider = metering.MeteredProvider(inner=_Inner(), session=session, tenant_id=me["tenant_id"], feature="copilot", user_id=me["id"])
            await provider.complete("SYSTEM", "question")
            await session.commit()
            return await session.scalar(select(LlmCallRecord.prompt_version).where(LlmCallRecord.user_id == me["id"]).order_by(LlmCallRecord.id.desc()).limit(1))
    assert _run(go()) == "unversioned:copilot"


def test_monitor_docstring_matches_the_code():
    text = (APP / "ai" / "monitor.py").read_text(encoding="utf-8")
    assert ".complete(" not in text and "provider" not in text.split('"""')[2]
    assert "No model is called anywhere in this module" in text

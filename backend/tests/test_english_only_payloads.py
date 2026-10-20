"""English only outside the strategy interview: the server-sent notice and consent texts, and the AI settings, carry no
second language and offer no language choice (the owner's ruling, Oct 2026; the interview is the one trilingual screen).

The repo-wide Devanagari check (scripts/check_english_only.py) catches script in source files; this catches the shape:
a `mr` / `hi` translation key coming back into the AI Copilot first-use notice or the DPDP data-sharing consent, a
"Read in Marathi" field, a second entry in the AI language list, or an acceptance recorded in another language.
"""
import asyncio
import json
import re

from sqlalchemy import select

from app.ai import compliance_terms as terms
from app.db.models import AiAcknowledgementRecord, ThesisRecord
from tests.test_auth_api import _register, _session_factory, client
from tests.test_trading_worker import _upgrade_plan

DEVANAGARI = re.compile("[\u0900-\u097f\ua8e0-\ua8ff]")
# A key naming another language: `mr`, `hi`, `text_mr`, `body_hi`, `marathi`, `read_in_marathi`, `secondary_text`, ...
SECOND_LANGUAGE_KEY = re.compile(r"^(mr|hi)$|_(mr|hi)$|^(mr|hi)_|marathi|hindi|secondary|translation", re.IGNORECASE)


def _keys(value, path="$"):
    if isinstance(value, dict):
        for k, v in value.items():
            yield f"{path}.{k}", str(k)
            yield from _keys(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _keys(v, f"{path}[{i}]")


def _assert_english_only(payload: dict) -> None:
    bad = [path for path, key in _keys(payload) if SECOND_LANGUAGE_KEY.search(key)]
    assert bad == [], bad
    text = json.dumps(payload, ensure_ascii=False)
    assert not DEVANAGARI.search(text)
    assert not re.search(r"read in (marathi|hindi)|\bmarathi\b|\bhindi\b", text, re.IGNORECASE)


def test_the_notice_and_consent_texts_are_english_only_at_the_source():
    assert set(terms.ACK_TEXT) == {"en"} and set(terms.DATA_CONSENT_TEXT) == {"en"}
    for kind in (terms.KIND_COPILOT, terms.KIND_DATA):
        _assert_english_only(terms.status_dict(kind, None))


def test_the_copilot_first_use_notice_payload_has_no_second_language():
    headers = {"Authorization": f"Bearer {_register('eo-notice@example.com', ai_terms=False)}"}
    for query in ("", "?language=mr", "?lang=hi"):
        status = client.get(f"/api/ai/acknowledgement{query}", headers=headers).json()
        assert set(status["text"]) == {"en"} and "SEBI" in status["text"]["en"]
        _assert_english_only(status)
    # An older client that still says "mr" is accepted, and the acceptance is recorded as English (what was shown).
    accepted = client.post("/api/ai/acknowledgement", headers=headers, json={"version": terms.ACK_VERSION, "language": "mr"})
    assert accepted.status_code == 200 and accepted.json()["accepted"] is True
    _assert_english_only(accepted.json())
    me = client.get("/api/auth/me", headers=headers).json()

    async def languages():
        async with _session_factory() as session:
            return list(await session.scalars(select(AiAcknowledgementRecord.language).where(AiAcknowledgementRecord.user_id == me["id"])))
    assert asyncio.run(languages()) == ["en"]


def test_the_dpdp_data_sharing_consent_payload_has_no_second_language():
    headers = {"Authorization": f"Bearer {_register('eo-consent@example.com')}"}
    me = client.get("/api/auth/me", headers=headers).json()
    _upgrade_plan(me["tenant_id"], "pro")
    consent = client.get("/api/ai/provider", headers=headers).json()["data_consent"]
    assert set(consent["text"]) == {"en"} and "outside India" in consent["text"]["en"]
    _assert_english_only(consent)
    saved = client.put("/api/ai/provider", headers=headers,
                       json={"provider": "anthropic", "api_key": "sk-ant-eo-consent-1234", "data_consent": True, "language": "mr"})
    assert saved.status_code == 200, saved.text
    _assert_english_only(saved.json()["data_consent"])


def test_the_ai_language_setting_offers_english_only():
    headers = {"Authorization": f"Bearer {_register('eo-prefs@example.com')}"}
    for prefs in (client.get("/api/ai/preferences", headers=headers).json(),
                  client.put("/api/ai/preferences", headers=headers, json={"ai_language": "mr"}).json()):
        assert prefs == {"ai_language": "en", "languages": [{"code": "en", "label": "English"}]}


def test_a_stored_thesis_defaults_to_english():
    assert ThesisRecord.__table__.c.lang.default.arg == "en"

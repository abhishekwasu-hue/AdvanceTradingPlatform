"""M72 PR-10 (CP-10, findings F4 / F16 / F17; M72Q-2 confirmed): the Copilot planner's answers in the user's language.

Scope (owner, M72Q-2): Copilot chat ANSWERS only. The interface, code, alerts, the rule answers (ATP's own fallback) and
every other AI surface stay English; the strategy interview keeps its own fixed translations (untouched).

* The choice lives in the user's own `user_prefs` namespace `ai` (`language`: en / mr / hi; `voice` for the browser's
  speech input) - no migration; read only while the flag `copilot_languages` is on for his organisation, else English.
* The planner is told to answer in Marathi / Hindi with every number, ticker, strike and symbol exactly as the tool
  data has it (Latin digits).
* The answer check stays fail-closed: the Marathi / Hindi buy / sell / order phrases (`indic_safety.answer_terms`, in
  `grounding.answer_problem`) refuse an answer; a Devanagari digit refuses it (digits stay as in the data, F16); the
  English / romanised advice words and trade calls are read on the text with its Devanagari taken out
  (`output_filter.check`), and any other script, hidden character or combining mark outside Devanagari is withheld
  exactly as before.
"""
from __future__ import annotations

import re
from typing import Any

FLAG = "copilot_languages"
PREFS = "ai"
LANGS = ("auto", "en", "mr", "hi")        # CH-1 (owner 5 Nov): "auto" = the language the question is written in
NAMES = {"en": "English", "mr": "Marathi", "hi": "Hindi"}

_DEV_DIGIT = re.compile("[\u0966-\u096f]")
# Devanagari letters, signs and marks (the block without its digits), and a joiner / non-joiner inside a Devanagari word
_DEV = re.compile("[\u0900-\u0965\u0970-\u097f\ua8e0-\ua8ff\u1cd0-\u1cff]+(?:[\u200c\u200d][\u0900-\u0965\u0970-\u097f]+)*")

ENGLISH_LINE = "5. Keep the answer short and plain. Answer in English only - also when the user writes in another language or script."
_LINE = ("5. Keep the answer short and plain. Answer in {name} (Devanagari script) - also when the user writes in English. "
         "Write every number, price, strike, ticker, symbol and unit exactly as the tool data has it, in the digits 0-9 "
         "(never Devanagari digits, never number words such as lakh or crore). Keep tool names and Not set as they are.")


# CH-1 (owner 5 Nov, "Default yes"): "auto" answers a question written in Devanagari in that language (Marathi or Hindi);
# any other question in English. The answer checks then read it as a Marathi / Hindi answer (the same checks for both).
_AUTO_LINE = ("5. Keep the answer short and plain. Answer in the language of the user's question - Marathi or Hindi, in "
              "Devanagari script. Write every number, price, strike, ticker, symbol and unit exactly as the tool data has it, "
              "in the digits 0-9 (never Devanagari digits, never number words such as lakh or crore). Keep tool names and Not "
              "set as they are.")
_DEV_LETTER = re.compile("[\u0900-\u0965\u0970-\u097f]")

# CH-1 (owner 5 Nov): the answer's register. "analyst" (default): an experienced market analyst's words, numbers first;
# "plain": the earlier short plain answer. Rule 4 (never buy / sell / enter / exit / targets) is untouched by either.
TONES = ("analyst", "plain")
_PLAIN = "5. Keep the answer short and plain."
_ANALYST = ("5. Write like an experienced market analyst: the key numbers first, then short points, using market terms "
            "precisely (trend, structure, swing high / low, demand / supply area, momentum, volume, open interest build-up, "
            "implied volatility). Describe; never advise. Organise the text into short sections, each opening with a "
            "Markdown heading line '## <title>' (for example Summary, Structure, Areas, Option chain, Scenarios, What to "
            "watch) - only the sections the tool results support, Summary first.")


def resolve(lang: str, question: str) -> str:
    """The answer language for this question: "auto" -> "auto_dev" when the question has Devanagari letters, else "en"."""
    if lang == "auto":
        return "auto_dev" if _DEV_LETTER.search(question or "") else "en"
    return lang if lang in NAMES else "en"


def filter_lang(lang: str) -> str:
    """The answer checks' language: an "auto_dev" answer is checked as a Marathi / Hindi one."""
    return "mr" if lang == "auto_dev" else lang


def with_tone(system: str, tone: str) -> str:
    """The planner prompt with the answer's register (the line that starts rule 5)."""
    if tone == "plain" or _PLAIN not in system:
        return system
    return system.replace(_PLAIN, _ANALYST, 1)


def has_devanagari_digit(text: str) -> bool:
    return bool(_DEV_DIGIT.search(text or ""))


def without_devanagari(text: str) -> str:
    """The text with each Devanagari run replaced by a space (what the English / romanised checks read)."""
    return _DEV.sub(" ", text or "")


def system_for(base: str, lang: str) -> str:
    """The planner prompt for `lang` (English: `base` unchanged, so its pinned version stays)."""
    if lang == "auto_dev" and ENGLISH_LINE in base:
        return base.replace(ENGLISH_LINE, _AUTO_LINE)
    if lang not in NAMES or lang == "en" or ENGLISH_LINE not in base:
        return base
    return base.replace(ENGLISH_LINE, _LINE.format(name=NAMES[lang]))


async def answer_language(session: Any, user: Any) -> str:
    """The user's own answer language while `copilot_languages` is on for his organisation; English otherwise."""
    from app.me import prefs
    from app.platform.controls import flag_enabled
    if not await flag_enabled(session, FLAG, user.tenant_id):
        return "en"
    value, _ = await prefs.read_one(PREFS, user, session)
    lang = value.get("language")
    return lang if lang in LANGS else "auto"


async def answer_tone(session: Any, user: Any) -> str:
    """CH-1: the user's own answer register (prefs `ai.tone`), "analyst" by default."""
    from app.me import prefs
    value, _ = await prefs.read_one(PREFS, user, session)
    tone = value.get("tone")
    return tone if tone in TONES else "analyst"


__all__ = ["FLAG", "PREFS", "LANGS", "has_devanagari_digit", "without_devanagari", "system_for", "answer_language"]

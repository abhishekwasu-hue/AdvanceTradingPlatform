"""M72 PR-1a (owner order): the Marathi and Hindi no-advice / no-order / exit / other-user word lists - Devanagari and
common romanised spellings - in ONE place, read by both checks:

* the ask check (`copilot_chat.refusal_kind`), which the Copilot chat, the older Copilot box and the agent all call
  before any tool runs or any model is called: an ask to buy / sell (advice), to place an order, to stop an exit, or
  to read another person's data is refused, the same as in English;
* the answer check (`grounding.answer_problem`), which every AI answer passes (the chat's wording and the agent's text,
  claims and disclaimers): a reply that tells the trader to buy / sell or that places an order in Marathi / Hindi words
  is never shown (ATP answers in English; Devanagari is also withheld by `output_filter`).

Devanagari has no reliable `\\b` (vowel signs are not word characters), so a Devanagari phrase matches as a plain
substring of the lower-cased, space-normalised text; a romanised phrase matches on word boundaries. The lists name
phrases, never single common words, so a concept question ("order म्हणजे काय?", "SL kya hota hai?") is not refused.
"""
from __future__ import annotations

import re

# --- asks --------------------------------------------------------------------------------------------------------------

_DEV_ADVICE = (
    # Marathi: "should I buy / sell / take ...?"
    "घ्यावा का", "घ्यावी का", "घ्यावे का", "घेऊ का", "घ्यायचा का", "घ्यायची का", "घ्यायचे का", "घ्यायला हवा का", "घ्यायला हवे का",
    "विकत घेऊ का", "विकत घ्यावा का", "विकू का", "विकावा का", "विकावी का", "विकावे का", "खरेदी करू का", "खरेदी करावी का",
    "खरेदी करावा का", "खरेदी करावे का", "विक्री करू का", "विक्री करावी का", "कोणता शेअर घ्यावा", "कोणता स्टॉक घ्यावा",
    "कोणते शेअर घ्यावे", "एंट्री घेऊ का", "entry घेऊ का",
    # Hindi: "should I buy / sell / take ...?"
    "खरीदूं", "खरीदूँ", "खरीद लूं", "खरीद लूँ", "खरीदना चाहिए", "खरीदनी चाहिए", "खरीदें क्या", "ले लूं", "ले लूँ", "लूं क्या",
    "लूँ क्या", "लेना चाहिए", "बेचूं", "बेचूँ", "बेच दूं", "बेच दूँ", "बेचना चाहिए", "बेचें क्या", "कौन सा शेयर खरीद",
    "कौनसा शेयर खरीद", "कौन सा स्टॉक खरीद", "कौनसा स्टॉक खरीद", "एंट्री लूं", "entry लूं",
)
_ROM_ADVICE = (
    "ghyava ka", "ghyavi ka", "ghyave ka", "gheu ka", "ghyaycha ka", "ghyaychi ka", "ghyayla hava ka", "vikat gheu ka", "viku ka",
    "vikava ka", "vikavi ka", "vikave ka", "kharedi karu ka", "kharedi karavi ka", "kharedi karava ka", "vikri karu ka",
    "konta share ghyava", "kontha share ghyava", "entry gheu ka",
    "kharidu", "kharidun kya", "kharid lu", "kharid loon", "kharid lu kya", "kharidna chahiye", "kharide kya", "le lu kya", "le loon kya",
    "lena chahiye", "bechu", "bech du", "bech doon", "bechna chahiye", "beche kya", "kaunsa share kharidu", "kaun sa share kharidu",
    "kaunsa stock kharidu", "entry lu kya",
)
_DEV_ORDER_VERBS = ("टाका", "टाक", "टाकून दे", "टाकून द्या", "लावा", "लाव", "लावून दे", "लगाओ", "लगा दो", "लगा दे", "लगाना", "लगा दीजिए",
                    "डालो", "डाल दो", "डाल दे", "place कर", "प्लेस कर", "कर दो", "कर दे", "करून दे", "करून द्या")
_ROM_ORDER_VERBS = ("tak", "taka", "taak", "taaka", "takun de", "lav", "lava", "laav", "lavun de", "laga", "laga do", "laga de", "lagao",
                    "daal", "daal do", "dal do", "dalo", "daalo", "place kar", "place karo", "kar do", "karo", "karun de")
_FOR_ME = ("माझ्यासाठी", "माझ्या साठी", "मेरे लिए", "मेरे लिये", "mere liye", "mere liya", "mazya sathi", "majhya sathi", "mazyasathi")
_TRADE_WORDS_DEV = ("order", "ऑर्डर", "खरेदी", "विक्री", "खरीद", "बेच", "buy", "sell")
_EXIT_WORDS = ("exit", "एक्झिट", "एग्जिट", "एक्जिट", "square off", "squareoff", "स्क्वेअर ऑफ", "स्क्वायर ऑफ")
_DEV_EXIT_STOP = ("मत ", "मत होने", "नको", "नका", "न होने", "ना होने", "होऊ देऊ नक", "होऊ नये", "रोको", "रोक दो", "रोक दे", "रोकना",
                  "रोक के", "थांबव", "थांबवा", "अडव", "ब्लॉक कर", "block कर", "बंद कर")
_ROM_EXIT_STOP = ("mat", "mat hone", "nako", "naka", "roko", "rok do", "rok de", "rokna", "rok ke", "thambav", "thambva", "adav", "adva",
                  "band kar", "band karo", "hone mat", "hou deu nako", "block kar")
_STOP_WORDS = ("sl", "stop loss", "stoploss", "stop-loss", "स्टॉप लॉस", "स्टॉपलॉस", "एसएल")
_DEV_STOP_OFF = ("बंद कर", "हटा", "काढून टाक", "काढ", "off कर", "मत लगा", "ignore कर", "लागू नको", "लागू करू नको", "रोको", "रोक दो")
_ROM_STOP_OFF = ("band kar", "band karo", "hata", "hata do", "hatao", "kadhun tak", "kadh", "off kar", "off karo", "ignore kar", "mat laga",
                 "lagu nako", "roko")
# "दुसऱ्या" / "दूसरे" alone also mean "second" ("my second trade"): only the "someone else's" forms are listed
_DEV_OTHER = ("दुसऱ्याच्या", "दुसर्‍याच्या", "दुसऱ्याचे", "दुसऱ्याची", "इतरांच्या", "इतर लोकांच्या", "त्याच्या", "तिच्या", "त्यांच्या",
              "दूसरों", "दूसरे लोगों", "दूसरे व्यक्ति", "उसके", "उसकी", "उनके", "उनकी", "किसी और", "किसी और के")
_ROM_OTHER = ("dusryachya", "dusryache", "itaranchya", "itar lokanchya", "tyachya", "tichya", "tyanchya", "dusron", "dusre logon",
              "doosron", "uske", "uski", "unke", "unki", "kisi aur")
_ACCOUNT_WORDS = ("position", "positions", "trade", "trades", "p&l", "pnl", "portfolio", "holding", "holdings", "पोझिशन", "पोजीशन",
                  "पोज़िशन", "ट्रेड", "होल्डिंग", "नफा", "तोटा", "मुनाफा", "नुकसान", "munafa", "nafa", "tota", "orders")

# --- answers: a reply that tells the trader to buy / sell or that places an order -----------------------------------------

_DEV_ANSWER = ("खरीद लो", "खरीद लीजिए", "खरीदो", "खरीदें", "बेच दो", "बेचो", "बेच दें", "बेचें", "ले लो", "घ्या", "घेऊन टाका", "विकत घ्या",
               "विका", "विकून टाका", "खरेदी करा", "विक्री करा", "ऑर्डर टाकली", "order टाकली", "ऑर्डर लगा दी", "order लगा दी",
               "पक्का फायदा", "पक्का मुनाफा", "नक्की फायदा", "गारंटी")
_ROM_ANSWER = ("kharid lo", "kharid lijiye", "kharido", "bech do", "becho", "le lo", "ghya", "ghyaa", "gheun taka", "vikat ghya", "vika",
               "vikun taka", "kharedi kara", "vikri kara", "order takli", "order laga di", "order laga diya", "pakka fayda", "pakka munafa",
               "nakki fayda")


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


def _rom(words) -> str:
    return rf"(?<![a-z])(?:{_alt(words)})(?![a-z])"


def _near(first, second, gap: int) -> str:
    """`first` then, within `gap` characters of the same sentence, `second` (both alternations already built)."""
    return rf"(?:{first})[^.?!\n]{{0,{gap}}}(?:{second})"


ASK_PATTERNS: dict[str, list[str]] = {
    "advice": [_alt(_DEV_ADVICE), _rom(_ROM_ADVICE)],
    "order": [
        _near(_alt(("order", "ऑर्डर", "ordar")), _alt(_DEV_ORDER_VERBS) + "|" + _rom(_ROM_ORDER_VERBS), 15),
        _near(_alt(_FOR_ME), _alt(_TRADE_WORDS_DEV) + "|" + _rom(("kharid", "kharedi", "bech", "vikri", "kharedi kar", "buy", "sell")), 40),
    ],
    "exit_block": [
        _near(_alt(_EXIT_WORDS), _alt(_DEV_EXIT_STOP) + "|" + _rom(_ROM_EXIT_STOP), 20),
        _near(_rom(_STOP_WORDS) + "|" + _alt(("स्टॉप लॉस", "स्टॉपलॉस", "एसएल")), _alt(_DEV_STOP_OFF) + "|" + _rom(_ROM_STOP_OFF), 15),
    ],
    "other_user": [_near(_alt(_DEV_OTHER) + "|" + _rom(_ROM_OTHER), _alt(_ACCOUNT_WORDS), 25)],
}
_ANSWER: re.Pattern[str] = re.compile(rf"{_alt(_DEV_ANSWER)}|{_rom(_ROM_ANSWER)}", re.IGNORECASE)


def answer_terms(text: str) -> list[str]:
    """The Marathi / Hindi buy / sell / order / promise phrases a reply uses (empty: none)."""
    return sorted({m.group(0).lower() for m in _ANSWER.finditer(re.sub(r"\s+", " ", text or ""))})


__all__ = ["ASK_PATTERNS", "answer_terms"]

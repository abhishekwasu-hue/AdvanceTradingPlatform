"""Z10-f: the questions the support bot turns away, before it searches anything.

* **advice** - what to buy, sell or hold, which stock or strike, price targets, "will X go up", tips, "is it a good time
  to", how much to invest - and any question that uses the platform's advice / guarantee words (`ai.output_filter`).
  The bot gives no investment advice: the platform is not a SEBI-registered adviser or research analyst.
* **orders** - a request to place, change, cancel or exit an order or a position ("sell my RELIANCE", "can you exit
  this", "square off for me"). The bot has no tool that touches orders. The reply says where the person does it
  (Positions for a paper exit) - an exit is never made harder.
* **account** - a question about the person's own positions, orders, trades, P&L, balance or holdings ("what is my
  P&L today", "how much did I lose"). The bot has no tool that reads them; the reply names the page that shows them.

How-to questions about the same things ("how do I close a position?", "where do I see my orders?") are answered from
the help pages - they are about using the platform, not about the person's account or a trade.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.ai import output_filter
from app.support.normalize import compact, deobfuscated, normalize

_HOWTO = re.compile(r"^\s*(?:how\s+(?:do|can|does|should|to|would)|how\s+is|where\b|is\s+there\b|can\s+i\b|could\s+i\b|do\s+i\b|"
                    r"does\b|what\s+(?:is|are)\s+(?:a|an|the)\b|what\s+does\b|why\s+(?:does|do|is|can)\b|help\s+me\s+(?:find|understand)\b)",
                    re.I)

_SHOULD = r"(?:should|shoud|shuld|shud|shld|shall|must)"
_ADVICE = [re.compile(p, re.I) for p in (
    rf"\b{_SHOULD}\s+(?:i|we)\s+(?:buy|sell|hold|short|invest|trade|exit|enter|book|average|add|keep|accumulate|purchase)\b",
    r"\bis\s+\S+(?:\s+\S+)?\s+a\s+(?:good\s+|strong\s+|safe\s+)?(?:buy|sell|short|hold)\b",
    r"\b(?:stocks?|shares?|scrips?|coins?|options?|strikes?|names|tickers?)\s+to\s+(?:buy|sell|short|pick|invest|trade|hold|accumulate)\b",
    r"\b(?:going\s+to|gonna)\s+(?:go\s+up|go\s+down|rise|fall|rally|crash|move\s+up|move\s+down)\b",
    r"\b(?:outlook|forecast|prediction|predict|bullish|bearish)\b",
    r"\b(?:will|would)\s+(?:make|give|earn)\s+(?:me\s+)?(?:a\s+)?(?:profit|money|returns?)\b",
    # Hinglish (Latin script) and Hindi
    r"\b(?:kaun|kon|kaunsa|konsa|kaunse|konse|kaun\s*sa|kon\s*sa|kaun\s*se|kon\s*se)\s+(?:share|stock|scrip|option|strike|coin)s?\b",
    r"\b(?:kharid\w*|khareed\w*|bechu|bechun|bechna|beche|bechein|becho|bech\s+du|bech\s+doon|le\s+lu|le\s+loon|lena\s+chahiye|"
    r"lu\s+kya|loon\s+kya|kya\s+lu|kya\s+loon|munafa|pakka\s+profit|paisa\s+double)\b",
    r"\b(?:tips?|target|call)\s+(?:do|dijiye|batao|bataiye|chahiye|bolo)\b",
    r"कौन\s*सा\s+शेयर|कौन\s*से\s+शेयर|कौनसा|खरीद(?:ना|ें|ूं|ूँ|ू|े|ो|नी)|बेच(?:ना|ें|ूं|ूँ|ू|े|ो|नी)|शेयर\s+ल(?:े|ें|ूं|ूँ)|"
    r"सलाह|टिप्स?|लक्ष्य|मुनाफ़?ा|फायदा\s+होगा",
    r"\b(?:which|what)\s+(?:stocks?|shares?|scrips?|options?|strikes?|coins?|crypto(?:s|currency)?|mutual\s+funds?|funds?|sectors?|"
    r"ipos?|futures?)\b.{0,40}\b(?:buy|sell|hold|invest|pick|trade|choose|short|go\s+up|rise|rally)\b",
    r"\b(?:stock|share|trading|intraday|option|options|f&o|crypto)\s+tips?\b",
    r"\btips?\s+(?:for|on)\s+(?:today|tomorrow|this\s+week|intraday|nifty|bank\s*nifty)\b",
    r"\bprice\s+target\b|\btarget\s+price\b|\bwhat\s+(?:is|will\s+be)\s+the\s+target\b",
    r"\bwill\s+\S+(?:\s+\S+)?\s+(?:go\s+up|go\s+down|rise|fall(?!\s+back)|rally|move\s+up|move\s+down)\b",
    r"\b(?:go\s+up|go\s+down|rise|fall|crash|rally)\s+(?:today|tomorrow|this\s+week|next\s+week)\b",
    r"\bis\s+(?:it|this|now)\s+(?:a\s+)?(?:good|right|safe)\s+(?:time|stock|share|buy|entry|idea|trade)\b",
    r"\b(?:good|safe)\s+(?:stocks?|shares?|buys?|investments?)\b",
    r"\b(?:best|top)\s+(?:stocks?|shares?|strateg(?:y|ies)|options?|strikes?|trades?|picks?|funds?|coins?)\b",
    r"\b(?:recommend|suggest|advise)\s+(?:me\s+)?(?:(?:a|an|some|any|the|which|good|best)\s+)?"
    r"(?:stocks?|shares?|options?|strikes?|strateg(?:y|ies)|funds?|coins?|trades?)\b",
    r"\b(?:recommend|suggest|advise)\b.{0,40}\b(?:buy|sell|invest)\b",
    r"\bhow\s+much\s+(?:should|to|must|can)\s+(?:i\s+)?(?:invest|buy|put|allocate|risk)\b",
    r"\bsure[- ]?shot\b|\bmultibagger\b|\bjackpot\b",
    # "should I go all in on TCS?", "all-in on banks?" - not "see them all in one place"
    r"\b(?:go|goes|going|went|gone|be|bet|put\s+(?:it|everything|my\s+\w+))\s+all[\s-]+in\b(?!\s+(?:one|a|the|the\s+same)\b)",
    r"\ball[\s-]+in\b(?!\s+(?:one|a|the|the\s+same)\b)\s+(?:on|with|into)\b|^\s*all[\s-]+in\b(?!\s+(?:one|a|the|the\s+same)\b)\s*[?!.]*\s*$",
    r"शिफारस|कोणता\s+शेअर|कोणते\s+शेअर|घ्यावा\s+का|घेऊ\s+का|विकू\s+का|खरेदी\s+करू\s+का|टिप्स",
)]
# Only outside a how-to clause: "RELIANCE: buy or sell?" is advice, "how do I buy or sell in paper mode?" is not.
_ADVICE_CLAUSE = [re.compile(p, re.I) for p in (
    r"\b(?:buy|sell|short)\s+or\s+(?:sell|hold|buy|not|wait|short)\b",
    r"^\s*[A-Za-z][A-Za-z0-9&.-]*\s*[:,-]?\s*(?:buy|sell|short|hold)\s*$",          # "TCS: buy?"
)]
# The same questions with the spaces taken out ("s h o u l d I b u y", "buy-or-sell").
_ADVICE_COMPACT = ("shouldibuy", "shouldisell", "shouldwebuy", "shouldwesell", "shouldiinvest", "shouldishort", "buyorsell",
                   "sellorbuy", "whichstocktobuy", "whichsharetobuy", "stockstobuy", "sharestobuy", "isitagoodbuy",
                   "targetprice", "pricetarget", "stocktips", "intradaytips", "kaunsashare", "konsashare", "konsastock",
                   "kaunsastock")

_ACT = r"(?:buy|sell|short|place|cancel|modify|amend|exit|close|square\s*-?\s*off|execute|book)"
_ORDERS = [re.compile(p, re.I) for p in (
    # an imperative at the start ("sell my INFY", "buy 50 TCS") - not a question about a control ("exit now button ...")
    rf"^\s*(?:please\s+|pls\s+|kindly\s+)?{_ACT}\b(?!.{{0,30}}\b(?:button|page|tab|dialog|screen|feature|link|menu|option\s+in)\b)",
    rf"\b(?:can|could|will|would)\s+you\s+(?:please\s+)?{_ACT}\b",
    rf"\b{_ACT}\b.{{0,40}}\bfor\s+me\b",
    rf"\b(?:i\s+want|i'd\s+like|i\s+would\s+like)\s+(?:you\s+)?to\s+{_ACT}\b",
    rf"\b(?:go\s+ahead\s+and|now)\s+{_ACT}\b",
)]

_ACCOUNT_THING = (r"(?:positions?|p\s*&\s*l|pnl|p/l|profits?|loss(?:es)?|holdings?|portfolio|orders?|trades?|balance|margin|"
                  r"funds?|capital|returns?|equity|account\s+value)")
_ACCOUNT = [re.compile(p, re.I) for p in (
    rf"\b(?:what(?:'s|\s+is|\s+are|\s+was|\s+were)?|how\s+much|how\s+many|show|tell|list|give|check|read|fetch|display)\b.{{0,25}}"
    rf"\b(?:my|our)\b.{{0,15}}\b{_ACCOUNT_THING}\b",
    r"\bhow\s+much\s+(?:did|have)\s+(?:i|we)\s+(?:make|made|lose|lost|earn|earned|gain|gained)\b",
    r"\b(?:am|are)\s+(?:i|we)\s+(?:in\s+)?(?:profit|loss|up|down)\b",
    r"\b(?:did|has|have)\s+(?:my|our)\s+(?:orders?|trades?|stop|target)\b",
    r"\bwhy\s+did\s+(?:my|our)\s+(?:orders?|trades?|positions?)\b",
    rf"^\s*(?:my|our)\s+{_ACCOUNT_THING}\b",
    rf"^\s*(?:show|list|display|fetch|give|get)\s+(?:me\s+)?(?:all\s+)?(?:the\s+)?(?:my\s+|our\s+)?{_ACCOUNT_THING}\b",
)]

REPLIES = {
    "advice": ("I can only help with how to use the platform. I can't say what to buy, sell or hold, or what a price will do: "
               "the platform gives no investment advice and is not a SEBI-registered adviser or research analyst. The "
               "decision is always yours; a SEBI-registered investment adviser can advise you."),
    "orders": ("I can't place, change, cancel or exit orders - I have no access to trading. To close a paper position, open "
               "Positions and close it from its row; the platform's stop-loss and end-of-day square-off keep running on their "
               "own. A real order is only ever placed by you, in your own broker app."),
    "account": ("I can't see your account - I don't read positions, orders, trades or P&L. Open Positions, Orders, Portfolio "
                "or Analytics to see them. I can explain how any of those pages works."),
}
LINKS = {
    "advice": [{"title": "Risk disclosure", "url": "/legal/risk", "source": "legal"}],
    "orders": [{"title": "Positions", "url": "/positions", "source": "help"}],
    "account": [{"title": "Positions", "url": "/positions", "source": "help"}, {"title": "Orders", "url": "/orders", "source": "help"}],
}


@dataclass(frozen=True)
class Refusal:
    kind: str           # advice | orders | account
    text: str

    def links(self, *, include_legal: bool = True) -> list:
        return [dict(link) for link in LINKS[self.kind] if include_legal or not link["url"].startswith("/legal/")]


_SPACED = re.compile(r"(?:(?<![^\W_])[^\W\d_](?:[\s._*-]+|$)){4,}")
_CLAUSES = re.compile(r"[.?!;\n]+|\balso\b|\band\s+then\b|\bthen\b|\bplus\b", re.I)
# A trade word written so a filter would not read it ("b u y", "8uy", "s e l l", "5e11") is refused as such.
_TRADE_WORD = re.compile(r"\b(?:buy|sell|short|hold|invest|exit|accumulate|purchase|square\s*off)\b", re.I)
_PRIORITY = ("advice", "orders", "account")


def classify(question: str) -> Optional[str]:
    """'advice', 'orders', 'account' or None (a question the bot may try to answer).

    The question is normalised first (`normalize`: NFKC, no zero-width characters, Latin look-alikes mapped). Advice is
    looked for in the whole question, with and without spaces. Orders and account questions are looked for clause by
    clause, so a how-to opening ("How do I run a backtest?") never covers a request in the next clause ("Also tell me
    my P&L today").

    The same checks then run on each de-disguised reading (`deobfuscated`: "b u y" -> "buy", "8uy" -> "buy"); a reading
    that has a trade word the question as typed does not have is refused as advice even in a how-to question - someone
    spelling "s e l l" apart is getting past a filter, not asking how a page works."""
    q = normalize(question)
    kinds = [_classify(q)]
    plain_trade_words = len(_TRADE_WORD.findall(q))
    for reading in deobfuscated(q):
        kinds.append(_classify(reading))
        if len(_TRADE_WORD.findall(reading)) > plain_trade_words:
            kinds.append("advice")
    return next((k for k in _PRIORITY if k in kinds), None)


def _classify(q: str) -> Optional[str]:
    flat = compact(q)
    spaced = bool(_SPACED.search(q))       # "s h o u l d I b u y": read it without the spaces
    if output_filter.blocked_terms(q) or any(p.search(q) for p in _ADVICE) or (spaced and any(w in flat for w in _ADVICE_COMPACT)):
        return "advice"
    clauses = [c.strip() for c in _CLAUSES.split(q) if c and c.strip()] or [q]
    found: Optional[str] = None
    for clause in clauses:
        if _HOWTO.search(clause):
            continue
        if any(p.search(clause) for p in _ADVICE_CLAUSE):
            return "advice"
        if any(p.search(clause) for p in _ORDERS):
            found = found or "orders"
        elif any(p.search(clause) for p in _ACCOUNT) and found is None:
            found = "account"
    return found


def check(question: str) -> Optional[Refusal]:
    kind = classify(question)
    return Refusal(kind=kind, text=REPLIES[kind]) if kind else None


__all__ = ["REPLIES", "Refusal", "check", "classify"]

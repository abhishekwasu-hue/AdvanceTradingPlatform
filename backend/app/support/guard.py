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

_HOWTO = re.compile(r"^\s*(?:how\s+(?:do|can|does|should|to|would)|how\s+is|where\b|is\s+there\b|can\s+i\b|could\s+i\b|do\s+i\b|"
                    r"does\b|what\s+(?:is|are)\s+(?:a|an|the)\b|what\s+does\b|why\s+(?:does|do|is|can)\b|help\s+me\s+(?:find|understand)\b)",
                    re.I)

_ADVICE = [re.compile(p, re.I) for p in (
    r"\bshould\s+(?:i|we)\s+(?:buy|sell|hold|short|invest|trade|exit|enter|book|average|add|keep)\b",
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
    r"शिफारस|कोणता\s+शेअर|घ्यावा\s+का|विकू\s+का|खरेदी\s+करू\s+का|टिप्स",
)]

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


def classify(question: str) -> Optional[str]:
    """'advice', 'orders', 'account' or None (a question the bot may try to answer)."""
    q = re.sub(r"\s+", " ", question or "").strip()
    if output_filter.blocked_terms(q) or any(p.search(q) for p in _ADVICE):
        return "advice"
    howto = bool(_HOWTO.search(q))
    if not howto and any(p.search(q) for p in _ORDERS):
        return "orders"
    if not howto and any(p.search(q) for p in _ACCOUNT):
        return "account"
    return None


def check(question: str) -> Optional[Refusal]:
    kind = classify(question)
    return Refusal(kind=kind, text=REPLIES[kind]) if kind else None


__all__ = ["REPLIES", "Refusal", "check", "classify"]

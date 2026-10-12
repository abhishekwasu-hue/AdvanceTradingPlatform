"""N1 F3: the portfolio risk view as short text - the Telegram `/risk` lines and the proposal card's "portfolio
impact" line (flag `portfolio_risk`).

Information for the person's own decision, never advice: the lines state numbers of their own PAPER book and their
own limits; they never say what to buy, sell, add or cut, and nothing here places, changes or closes an order (the AI
never places one). `ADVICE_WORDS` is checked by the tests on every line built here.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.portfolio import caps as C
from app.portfolio import risk as R

# Words a line may never contain (SCREENER_SPEC §6 and the Copilot's advice list, plus imperatives about the book).
ADVICE_WORDS = ("recommend", "advice", "advise", "should", "must", "buy", "sell", "reduce", "cut your", "add more", "hedge now",
                "guarantee", "best", "safe bet", "target price")
# Marathi imperatives and advice words (review of #219): "buy", "sell", "reduce", "must / should", "advice", "buy (take)".
ADVICE_WORDS_MR = ("खरेदी करा", "विका", "विकून", "कमी करा", "पाहिजे", "हवे", "सल्ला", "विकत घ्या", "घ्या", "करावे")
_ADVICE_EN = re.compile(r"\b(" + "|".join(re.escape(w) for w in ADVICE_WORDS) + r")\b", re.IGNORECASE)


def has_advice(line: str) -> bool:
    """True when a line holds an English advice word (whole words) or a Marathi one (as written)."""
    return bool(_ADVICE_EN.search(line)) or any(w in line for w in ADVICE_WORDS_MR)


def _tr(lang: str, en: str, mr: str) -> str:
    return mr if lang == "mr" else en


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:g}%"


def _money(value: float) -> str:
    return f"{value:,.0f}"


def telegram_lines(view: Dict[str, Any], lang: str = "en", limits: Optional[C.Limits] = None) -> List[str]:
    """Three to five lines of the user's own PAPER book (the `GET /api/portfolio/risk` view)."""
    if not view.get("positions"):
        return [_tr(lang, "Portfolio (your PAPER book): no open positions.", "Portfolio (तुमचे PAPER book): उघडी position नाही.")]
    ex, risk = view["exposure"], view["open_risk"]
    out = [_tr(lang, f"Portfolio (your PAPER book): {view['positions']} positions, exposure {_money(ex['gross'])} ({_pct(ex['gross_pct'])} "
                     f"of capital), net {ex['net']:+,.0f}.",
               f"Portfolio (तुमचे PAPER book): {view['positions']} positions, exposure {_money(ex['gross'])} (भांडवलाच्या "
               f"{_pct(ex['gross_pct'])}), net {ex['net']:+,.0f}.")]
    top = view["buckets"][0] if view.get("buckets") else None
    tail = (_tr(lang, f"; largest bucket {top['bucket']} {_money(top['gross_notional'])} ({_pct(top['gross_pct'])})",
                f"; सर्वात मोठा bucket {top['bucket']} {_money(top['gross_notional'])} ({_pct(top['gross_pct'])})") if top else "")
    out.append(_tr(lang, f"Open risk at the stops: {_money(risk['total'])} ({_pct(risk['pct'])} of capital){tail}.",
                   f"Stops ला उघडी जोखीम: {_money(risk['total'])} (भांडवलाच्या {_pct(risk['pct'])}){tail}."))
    beta = view["beta"]
    unknown = (_tr(lang, f"; beta unknown for {', '.join(beta['unknown'][:5])} (counted as 1)",
                   f"; {', '.join(beta['unknown'][:5])} चा beta माहीत नाही (1 धरला)") if beta.get("unknown") else "")
    out.append(_tr(lang, f"NIFTY 50 beta-weighted net exposure: {beta['net']:+,.0f} ({_pct(beta['net_pct'])}){unknown}.",
                   f"NIFTY 50 beta-weighted net exposure: {beta['net']:+,.0f} ({_pct(beta['net_pct'])}){unknown}."))
    warning = (view.get("direction") or {}).get("warning")
    if warning:
        out.append(_tr(lang, warning, "सर्व दिशात्मक positions एकाच बाजूला आहेत: एकाच मोठ्या उलट हालचालीत ती सगळी एकत्र तोट्यात जाऊ शकतात."))
    if limits is not None and limits.any:
        parts = []
        if limits.max_sector_pct:
            parts.append(_tr(lang, f"one bucket's exposure {limits.max_sector_pct:g}%", f"एका bucket चे exposure {limits.max_sector_pct:g}%"))
        if limits.max_correlated_risk_pct:
            parts.append(_tr(lang, f"one bucket's open risk {limits.max_correlated_risk_pct:g}%",
                             f"एका bucket ची उघडी जोखीम {limits.max_correlated_risk_pct:g}%"))
        if limits.max_open_positions:
            parts.append(_tr(lang, f"{limits.max_open_positions} open positions", f"{limits.max_open_positions} उघड्या positions"))
        out.append(_tr(lang, "Your limits on new entries: " + ", ".join(parts) + ".", "नवीन entries वरील तुमच्या मर्यादा: " + ", ".join(parts) + "."))
    return out


def impact(inputs: C.BookInputs, candidate: C.Candidate) -> Dict[str, Any]:
    """The user's book after this entry, taken at its most (`caps.sized`): the entry's bucket's exposure and open risk,
    all open risk, and the positions, each against the user's limit when one is set. Pure."""
    size = C.sized(candidate, inputs.risk_cap_amount)
    bucket = inputs.buckets.get(candidate.underlying) or f"{R.UNCLASSIFIED}:{candidate.underlying}"
    in_bucket = [p for p in inputs.positions if (inputs.buckets.get(p.underlying) or f"{R.UNCLASSIFIED}:{p.underlying}") == bucket]
    cap = inputs.capital

    def pct(v: float) -> Optional[float]:
        return round(v / cap * 100.0, 2) if cap > 0 else None
    gross_pct = pct(sum(p.gross for p in in_bucket) + size["notional"])
    risk_pct = pct(sum(p.risk for p in in_bucket) + size["risk"])
    total_pct = pct(sum(p.risk for p in inputs.positions) + size["risk"])
    out: Dict[str, Any] = {"bucket": bucket, "bucket_gross_pct": gross_pct, "bucket_risk_pct": risk_pct, "total_risk_pct": total_pct,
           "positions": len(inputs.positions) + 1, "entry_risk": round(size["risk"], 2),
           "limits": {"max_sector_pct": inputs.limits.max_sector_pct, "max_correlated_risk_pct": inputs.limits.max_correlated_risk_pct,
                      "max_open_positions": inputs.limits.max_open_positions}}
    lim = inputs.limits

    def of(limit: float) -> str:
        return f" (your limit {limit:g}%)" if limit else ""
    positions = f"{out['positions']}" + (f" (your limit {lim.max_open_positions})" if lim.max_open_positions else "")
    out["line"] = (f"Portfolio after this entry (at most): {bucket} exposure {_pct(gross_pct)} of capital"
                   f"{of(lim.max_sector_pct)}, open risk in {bucket} {_pct(risk_pct)}{of(lim.max_correlated_risk_pct)}, "
                   f"all open risk {_pct(total_pct)}, open positions {positions}.")
    return out


async def proposal_impact(session: Any, user: Any, symbol: str, entry: Any, stop: Any, *, quantity: Any = None,
                          now: Any = None, book: Optional[C.BookRead] = None) -> Optional[Dict[str, Any]]:
    """The impact of an entry proposal on its owner's own book (None with `portfolio_risk` off or when it cannot be
    read - it is information, so a problem never stops the proposal). `book`: this request's read of the owner's book
    (`caps.read_book`), reused; every read runs in a savepoint (the worker's shared session)."""
    from app.platform.controls import flag_enabled
    from app.portfolio import risk_data
    if user is None or not await flag_enabled(session, risk_data.FLAG, user.tenant_id):
        return None
    book = book if book is not None else await C.read_book(session, user, now)
    if book.inputs is None:
        return None
    try:
        inputs, candidate = await C.with_candidate(session, book.inputs, C.candidate_of(symbol, entry, stop, quantity=quantity), now)
    except Exception:  # noqa: BLE001 - no line rather than a wrong one (an unresolvable underlying included)
        return None
    return impact(inputs, candidate)

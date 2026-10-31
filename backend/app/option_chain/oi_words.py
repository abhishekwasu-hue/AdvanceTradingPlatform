"""M61 follow-up (owner order M61, OI-5; CLAUDE.md section 3): the OI banner's internal labels in neutral words.

The engine (oi_regime.py) keeps its internal values - BULLISH / BEARISH / MIXED / NEUTRAL directions, the stable
OI-lead signal, the five PCR bands - unchanged; this module only maps them to the text a USER reads (notifications,
the day digest). Never "bullish / bearish / overbought / oversold", never an action word: what the numbers did.

* Banner direction: the put / call OI-premium classes behind it (BULLISH = put premium down or call premium up).
* Stable signal: which side's total OI leads (diff = put OI - call OI) and whether the lead grew.
* PCR band: the option chain's zone names (dish.py: very low / low / balanced / high / very high).
* Option-chain analysis bias (analysis.py, the scanner filter and the signal score's option-chain note): the PCR and
  OI-change facts it rests on.
"""
from __future__ import annotations

from typing import Optional

DIRECTION_WORDS = {
    "BULLISH": "Put premium down / call premium up",
    "BEARISH": "Put premium up / call premium down",
    "MIXED": "Put and call readings differ",
    "NEUTRAL": "No clear OI change",
}

LEAD_WORDS = {"BULLISH": "Put OI lead", "BEARISH": "Call OI lead", "NEUTRAL": "No OI lead"}
STRENGTH_WORDS = {"STRONG": "growing", "WEAKENING": "not growing"}

PCR_BAND_WORDS = {
    "OVERSOLD": "Very low PCR zone",
    "MILD_BEARISH": "Low PCR zone",
    "SIDEWAYS": "Balanced PCR zone",
    "MILD_BULLISH": "High PCR zone",
    "OVERBOUGHT": "Very high PCR zone",
}

CLASS_WORDS = {
    "WRITING": "OI up, premium down",
    "BUYING": "OI up, premium up",
    "SHORT COVERING": "OI down, premium up",
    "LONG UNWINDING": "OI down, premium down",
    "FLAT/UNCLEAR": "no clear change",
    "INSUFFICIENT DATA": "not known yet",
}


# The option-chain analysis' bias (analysis.py: BULLISH only when PCR > 1.2 AND put OI builds while call OI is flat or
# falling; BEARISH the mirror; CONFLICTING when the two disagree) as what the two numbers show.
CHAIN_READ_WORDS = {
    "BULLISH": "PCR above 1.2, put OI building",
    "BEARISH": "PCR below 0.8, call OI building",
    "CONFLICTING": "PCR and OI change differ",
    "NEUTRAL": "No one-sided read",
}


def _key(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().upper()


def direction_words(direction: object) -> str:
    return DIRECTION_WORDS.get(_key(direction), DIRECTION_WORDS["NEUTRAL"])


def lead_words(label: object) -> str:
    """A stable-signal label ("BULLISH (Strong)", "BEARISH Weakening", "NEUTRAL") as "Put OI lead, growing"."""
    text = _key(label)
    side = "BULLISH" if "BULLISH" in text else "BEARISH" if "BEARISH" in text else "NEUTRAL"
    strength = "WEAKENING" if "WEAKENING" in text else "STRONG" if "STRONG" in text else None
    words = LEAD_WORDS[side]
    return f"{words}, {STRENGTH_WORDS[strength]}" if strength and side != "NEUTRAL" else words


def pcr_band_words(band: object) -> str:
    return PCR_BAND_WORDS.get(_key(band), "PCR zone not known")


def chain_read_words(bias: object) -> str:
    return CHAIN_READ_WORDS.get(_key(bias), CHAIN_READ_WORDS["NEUTRAL"])


def class_words(oi_class: object) -> str:
    return CLASS_WORDS.get(_key(oi_class).replace("_", " "), "not known yet")


def oi_read(diff: Optional[float], pcr: Optional[float]) -> str:
    """The neutral one-line read (the option chain's OI tab): "Put OI above call OI (PCR 1.04)"."""
    p = "PCR –" if pcr is None else f"PCR {pcr:.2f}"
    if diff is None:
        return p
    if diff > 0:
        return f"Put OI above call OI ({p})"
    if diff < 0:
        return f"Call OI above put OI ({p})"
    return f"Put OI equal to call OI ({p})"


def alert_state_words(alert_type: str, state: Optional[str]) -> str:
    """An alert's old / new state in the words above; states without an internal label (max pain, DTE, an OI wall
    "CE@25000") are already neutral and stay as they are."""
    if state is None:
        return "—"
    if alert_type == "DIRECTION_CHANGE":
        return direction_words(state)
    if alert_type in ("STABLE_FLIP", "STRENGTH_CHANGE"):
        return lead_words(state)
    if alert_type == "PCR_BAND":
        return pcr_band_words(state)
    return state


__all__ = ["DIRECTION_WORDS", "LEAD_WORDS", "PCR_BAND_WORDS", "CLASS_WORDS", "CHAIN_READ_WORDS", "direction_words", "lead_words",
           "pcr_band_words", "chain_read_words", "class_words", "oi_read", "alert_state_words"]

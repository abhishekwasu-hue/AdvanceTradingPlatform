"""M61 follow-up (OI-5): every OI / PCR label a user reads is in neutral words - never bullish / bearish / overbought /
oversold - while the engine's internal values stay unchanged."""
import re

from app.option_chain import oi_regime, oi_words

VERDICT = re.compile(r"\b(bullish|bearish|overbought|oversold|mildly|buy|buying|sell|selling)\b", re.I)


def test_every_internal_label_has_neutral_words():
    for d in oi_regime.Direction:
        assert not VERDICT.search(oi_words.direction_words(d))
    for band in oi_regime.PcrBand:
        words = oi_words.pcr_band_words(band)
        assert words != "PCR zone not known" and not VERDICT.search(words)
    for c in oi_regime.OIClass:
        words = oi_words.class_words(c)
        assert words != "not known yet" or c == oi_regime.OIClass.INSUFFICIENT_DATA
        assert not VERDICT.search(words)
    for bias in ("BULLISH", "BEARISH", "CONFLICTING", "NEUTRAL", None):
        assert not VERDICT.search(oi_words.chain_read_words(bias))
    for label in ("BULLISH (Strong)", "BULLISH (Weakening)", "BEARISH (Strong)", "BEARISH Weakening", "NEUTRAL"):
        assert not VERDICT.search(oi_words.lead_words(label))
    for kind in oi_regime.ALERT_TYPES:
        for state in ("BULLISH", "BEARISH", "MIXED", "NEUTRAL", "BULLISH Strong", *[b.value for b in oi_regime.PcrBand]):
            if kind in ("DIRECTION_CHANGE", "STABLE_FLIP", "STRENGTH_CHANGE", "PCR_BAND"):
                assert not VERDICT.search(oi_words.alert_state_words(kind, state)), (kind, state)


def test_the_words_follow_the_numbers():
    assert oi_words.direction_words("BULLISH") == "Put premium down / call premium up"
    assert oi_words.direction_words(oi_regime.Direction.BEARISH) == "Put premium up / call premium down"
    assert oi_words.lead_words("BULLISH (Strong)") == "Put OI lead, growing"
    assert oi_words.lead_words("BEARISH Weakening") == "Call OI lead, not growing"
    assert oi_words.lead_words("NEUTRAL") == "No OI lead"
    assert oi_words.pcr_band_words("OVERSOLD") == "Very low PCR zone" and oi_words.pcr_band_words(None) == "PCR zone not known"
    assert oi_words.class_words(oi_regime.OIClass.WRITING) == "OI up, premium down"
    assert oi_words.class_words("SHORT_COVERING") == "OI down, premium up"
    assert oi_words.oi_read(240.0, 1.04) == "Put OI above call OI (PCR 1.04)"
    assert oi_words.oi_read(-180.0, 0.82) == "Call OI above put OI (PCR 0.82)"
    assert oi_words.oi_read(0.0, None) == "Put OI equal to call OI (PCR –)"
    assert oi_words.alert_state_words("OI_WALL", "formed CE@25000") == "formed CE@25000"
    assert oi_words.alert_state_words("DTE", None) == "—"


def test_the_signal_score_note_uses_neutral_words_and_keeps_the_score():
    from types import SimpleNamespace

    from app.core.enums import SignalDirection
    from app.option_chain.models import OptionChainBias
    from app.signal_scoring.engine import _option_chain_component

    for direction in (SignalDirection.LONG, SignalDirection.SHORT):
        for bias in OptionChainBias:
            _, note = _option_chain_component(SimpleNamespace(direction=direction), SimpleNamespace(bias=bias))
            assert note.startswith("Option chain: ") and not VERDICT.search(note), note
    long_ = SimpleNamespace(direction=SignalDirection.LONG)
    assert _option_chain_component(long_, SimpleNamespace(bias=OptionChainBias.BULLISH)) == (100.0, "Option chain: PCR above 1.2, put OI building, on the signal's side")
    assert _option_chain_component(long_, SimpleNamespace(bias=OptionChainBias.BEARISH))[0] == 0.0
    assert _option_chain_component(long_, SimpleNamespace(bias=OptionChainBias.CONFLICTING))[0] == 30.0


def test_the_engine_keeps_its_internal_values():
    assert {d.value for d in oi_regime.Direction} >= {"BULLISH", "BEARISH", "MIXED", "NEUTRAL"}
    assert [b.value for b in oi_regime.PcrBand] == ["OVERSOLD", "MILD_BEARISH", "SIDEWAYS", "MILD_BULLISH", "OVERBOUGHT"]

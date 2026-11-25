import asyncio
from datetime import datetime, timezone

from app.screener.breakouts.amw_breakout import Settings
from app.workers.amw_breakout_signals import run_closed


def test_disabled_and_missing_bar_cannot_publish(monkeypatch):
    clock = datetime(2026, 11, 24, 4, 0, tzinfo=timezone.utc)

    async def unexpected(*args, **kwargs):
        raise AssertionError("Signal publication must not run")

    monkeypatch.setattr("app.workers.amw_breakout_signals.publish", unexpected)
    kwargs = dict(symbol="NIFTY", instrument_key="NSE_INDEX:NIFTY", now=clock)
    assert asyncio.run(run_closed(None, None, {}, Settings(), **kwargs)).reason == "DISABLED"
    assert asyncio.run(run_closed(None, None, {}, Settings(), enabled=True, **kwargs)).reason == "DUE_BAR_UNAVAILABLE"


def test_scan_facts_reused_only_for_compatible_settings(monkeypatch):
    from dataclasses import replace

    from app.workers.amw_breakout_signals import ScanFacts

    reads = []
    monkeypatch.setattr("app.workers.amw_breakout_signals.iter_snapshots",
                        lambda frames, cfg, **kwargs: reads.append(cfg) or ())
    frames = {}
    facts = ScanFacts(frames)
    cfg = Settings()
    clock = datetime(2026, 11, 24, 4, 0, tzinfo=timezone.utc)
    kwargs = {"as_of": clock, "instrument_is_index": False}
    facts.decisions(frames, cfg, **kwargs)
    facts.decisions(frames, replace(cfg, direction="LONG_ONLY"), **kwargs)
    facts.decisions(frames, replace(cfg, buffer=0.3), **kwargs)
    assert (facts.hits, facts.misses, len(reads)) == (1, 2, 2)

    import pytest
    with pytest.raises(ValueError, match="one frame batch"):
        facts.decisions({}, cfg, **kwargs)

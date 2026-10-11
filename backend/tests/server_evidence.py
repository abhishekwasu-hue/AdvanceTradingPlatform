"""H-C1 a test helper: make the server's own candle fetch (app.ai.evidence.server_frame) return the given candles, as a
broker session would - so tests exercise the server-evidence path instead of posting candles."""
from app.ai import evidence, interview
from app.core.models import OHLCVBar


def serve_candles(monkeypatch, candles, source: str = "broker:testbroker"):
    bars = [c if isinstance(c, OHLCVBar) else OHLCVBar(**c) for c in candles]
    calls = []

    async def fake(session, tenant_id, symbol, exchange, timeframe, *, broker=None, lookback_days=30):
        calls.append({"tenant_id": tenant_id, "symbol": symbol, "timeframe": timeframe})
        return interview.frame_from_candles(bars), source
    monkeypatch.setattr(evidence, "server_frame", fake)
    return calls

# M86 plan - book-knowledge chart reader in Copilot (order: docs/owner-orders/ATP_ORDER_M86.md)

Public repo: this file names interfaces only. The method (prompts, the 17 book specs, schemas, sanitize, request
building) lives in atp-private (`atp_private.chart_reader`), copied from trade-data branch ccr-7e74426e-fiv6z2
(head 6dd8bf4: abhi_read_v5, book_strategy_read_v5). Nothing of it is written, logged or printed by ATP.

## Reuse (no second copy of anything)
- Chart image: the M59 hybrid path renders stored lake candles (`app/chart_analyze/hybrid.py` `check_pick` /
  `build_reading`, `app/chart_analyze/reading_png.py` `render`).
- FACTS: `hybrid.facts(reading, ...)` formatted as plain text lines.
- Usage log: `llm_calls` (feature `chart_reader`, tokens, `cost_usd`; prompt / response text NOT stored - the columns
  get the fixed text "[private method - not stored]" and hashes only).
- Spend limits: `app/chart_analyze/limits.py` patterns; the daily cap is the user's own setting (prefs), OFF by default.
- Loader pattern: `app/chart_analyze/provider.py` / `app/engine_provider/loader.py` (import `atp_private.*` if present,
  else "not installed"; log name + version only).

## Contract: atp_private.chart_reader
```python
def get_reader() -> Reader            # raises nothing at import time; data files read from the package (or ATP_CHART_READER_DIR)
class Reader:
    name: str                          # "chart_reader"
    version: str                       # e.g. "abhi_read_v5+book_strategy_read_v5"
    ABHI: str = "ABHI"
    def strategies(self) -> list[dict]:    # [{"id","name","book","author","edition","two_chart":bool,"sides":[...]}]  labels only
    def two_chart_conditions(self, strategy: str) -> list[str]
    def read(self, *, instrument: str, charts: list[tuple[str, bytes, str]],   # (label, image bytes, media type)
             facts: str, strategy: str, model: str, effort: str, client) -> dict:
        # exactly chart_read.read_charts' result: status ok/refused/error, strategy{id,name,(independent_book_strategy)},
        # prompt_version, read, removed, time_flags, two_chart_conditions, second_chart_missing, usage{input_tokens,
        # output_tokens, cache_read_input_tokens, model}. sanitize(), schemas, refusal handling, server-side refusal
        # fallback and system-prompt caching unchanged; client = an anthropic.Anthropic (sync).
```
Tests in atp-private: the 18 offline tests ported + a fake-client test of `read`.

## Public side: app/chart_reader/ (W1, W2, W4)
- `loader.py` - load()/available()/reason (as provider.py).
- `settings.py` - platform settings with defaults: model `claude-opus-5-5`, effort `high`, api_key_env
  `CHART_READ_API_KEY` (ANTHROPIC_API_KEY as fallback), chart timeframes `["60min", "15min"]`, pair
  `{"NIFTY 50": "NIFTY BANK", "NIFTY BANK": "NIFTY 50"}`, cache_hours 24, price per MTok (input / output / cache-read)
  for the estimate, max_output_tokens.
- `service.py` - `read(session, user, tenant, symbol, strategy, *, source="lake"|"upload", image=None)`:
  lake: render each chart timeframe via the M59 path + FACTS -> "Advanced analysis (code+AI)";
  upload: the user's image, no FACTS -> "AI only".
  W2: when the strategy has two-chart conditions and the symbol has a pair, the pair's chart (same timeframe, same
  bars window) is attached automatically. Cache key: (tenant, strategy, prompt version, model, effort, symbol,
  timeframes, the layer fingerprint of every chart) - a hit returns the stored result with `cached: true` and costs
  nothing. Cost cap: user's `chart_reader_daily_cap_usd` (prefs, null = off); refused with a plain sentence when the
  day's spend + estimate would pass it. Every run writes one llm_calls row (tenant, user, tokens, cost).
- `routes.py` - `GET /api/chart-reader/strategies` (labels, default ABHI, estimate per read, model, effort, available),
  `POST /api/chart-reader/read` {symbol, strategy, source, upload_id?} -> the result + `{cached, estimate_usd, cost_usd,
  label: "Advanced analysis (code+AI)"|"AI only"}`. Flag `chart_reader` (default OFF, in the catalog/admin list).
- No order path is touched (ADR-0006). No migration (prefs JSON, llm_calls, Redis cache).

## UI (W3) - Copilot, the M59 Analyze-a-chart panel, opens in place
- "Abhi's method" card (default): trend, buyer / seller areas, correction, commitment candle, area-of-interest
  sentence, watch next, cannot_read; label chip AI only / Advanced analysis (code+AI); estimated cost; cached chip.
- "Book strategies" dropdown in the same panel: each read is its own card titled "<book> strategy", condition chips
  met / not_met / unclear, side_scored, trigger status + bar, invalidated, statement; two-chart note when a condition
  needs the pair and it was missing. Never merged into Abhi's card, never a filter or gate.
- Numbers first, explanations behind (?), phone 390 first, tokens only, English UI.

## Proof
Real API reads on 3 real charts (lake data): Abhi card + 2 book cards (one two-chart). Screenshots 390 before/after;
usage / cost per run reported. Run locally with the private package on PYTHONPATH and CHART_READ_API_KEY; images and
results go to the private trade-data repo only (never this repo).

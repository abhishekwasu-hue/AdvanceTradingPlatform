# ATP order M86 - book-knowledge chart reader in Copilot (owner, 3 Nov 2026) - verbatim

Note: "M85" inside the order is the trade-data order (the book-knowledge chart reader), not ATP M85 (Upstox token).

```text
M86 (ATP) - Wire the book-knowledge chart reader (M85) into Copilot. Owner order, 3 Nov 2026. Read OWNER_RULES first. Reply in Marathi.

SOURCE (PRIVATE repo trade-data, branch ccr-7e74426e-fiv6z2, PR #3):
- books/ai/chart_read.py: Claude vision reader. Mode ABHI = Abhi's method (default). Mode <BOOK_ID> = one independent book strategy. Tested: 108 real API reads, all ok, about $0.07 per read (latest v3 run: $7.62 for 108 reads); report in books/ai/tests/vision_test_2026-11-03/REPORT.md, plus the strategy-wise HTML report charts_report_api_results_v3.html.
- books/ai/prompts/*.md (current: abhi_read_v5, book_strategy_read_v4; always take the latest version on the branch) and books/strategies/<ID>.md (17 specs; the section 8 JSON is what the code loads).
- ATP is PUBLIC: copy prompts/, strategies/ and the reader logic into atp-private and load them at runtime from a private path/env. No prompt text, strategy content or chart data in the ATP repo, logs or CI output.

BUILD (one PR, wave by wave):
W1 Service: a backend service "chart_reader" that takes (instrument, chart image(s) rendered by the existing renderer from the data lake, FACTS text from the engine's levels, strategy id) and returns the JSON exactly as chart_read.read_charts does: strategy label, prompt_version, read, removed, time_flags, two_chart_conditions, second_chart_missing, usage. Keep sanitize(), the JSON schemas, refusal handling, refusal fallback and prompt caching unchanged. Model and effort are settings, not code (default claude-opus-5-5, high). The API key lives in server settings / secret store (env name configurable; note that the host may hide ANTHROPIC_* variables, so support CHART_READ_API_KEY).
W2 P6 second chart: for strategies whose spec has "needs: two charts" conditions, attach a second chart automatically (NIFTY <-> BANKNIFTY, same bars). The pair is a setting.
W3 UI (Copilot panel, M59 chart upload/hybrid, M49 design, no-navigation rule: everything opens in place):
   - Default card "Abhi's method": trend, buyer/seller areas, correction, commitment candle, area-of-interest sentence, watch next, cannot_read.
   - "Book strategies" dropdown in the same panel. Each read is its own card titled with the book name, e.g. "Gann, Truth of the Stock Tape (1923) strategy", with the conditions as met / not_met / unclear chips, side_scored, the trigger status and bar, invalidated, and the statement. Never merge book cards into Abhi's card and never use them as a filter or gate.
   - Labels "AI only" / "Advanced analysis (code+AI)". Wording is "area of interest"; no buy/sell calls (SEBI RA). Explanations behind (?), numbers first, phone 390 px first.
W4 Cost: usage is logged per run and per tenant. The cost cap per day is a user setting (OFF by default, per the risk rule), and the screen shows the estimated cost per read. A read is cached while the chart structure is unchanged (as V1/P4 do).
RULES: owner rule (3 Nov): no buy/sell/hold words in any form in AI output (buyer/seller area is allowed); sanitize() strips them. The AI only reads and explains; it never places, blocks or changes orders (ADR-0006). No day/date/session/clock framing (C.4). No SL/target/lots/strike/R:R in AI output (C.3; stripped). No new trading rules. Book strategies stay independent and named after their books.
TESTS: port books/ai/tests/test_chart_read.py (offline, 16 tests); add service tests with a fake client; no live API calls in CI.
PROOF: before/after phone screenshots (390 px) on 3 real charts: the Abhi card, plus at least 2 book-strategy cards, one of them a two-chart strategy. Report usage/cost for those runs.
REPORT: status by wave, screenshots, ONE owner-action block (API key in server settings, cost-cap choice), migration yes/no, next step.

ADDENDUM (3 Nov, review of all 17 specs; verified by a real-API re-run, 18 reads, $1.39, 0 sentences removed; commit b77b9d1): prompt now book_strategy_read_v5 (abhi_read_v5 unchanged). Section-8 spec texts use the neutral vocabulary only (no buy/sell/hold words reach the prompt); every spec carries `sides` (the sides the book states) and `trigger.stated`; two-chart conditions are all marked `needs: two charts`; position/P&L/tape invalidations moved to section 6. Full table: trade-data/books/strategies/README.md, section "Review 2026-11-03". Tests: 18 offline. Take the branch head, not an older copy.
```

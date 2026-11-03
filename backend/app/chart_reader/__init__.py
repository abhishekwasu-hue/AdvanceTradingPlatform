"""M86 (owner order ATP_ORDER_M86; docs/design/M86_PLAN.md): the book-knowledge chart reader in the Copilot.

The reader itself - its instructions, the book strategies, the answer schemas and the clean-up of the answer - lives in
the private package only (`loader.py` is the one module here that names it). ATP's public side holds the boundary:

- `loader`   imports the private reader once (None + a plain reason when it is absent; logs its name and version only);
- `settings` the platform settings with their code defaults (model, effort, the API key's environment name, the chart
             timeframes, the NIFTY 50 <-> NIFTY BANK pair, the cache hours, the prices for the estimate);
- `service`  one read: ATP's own rendered charts of the stored candles plus ATP's structure facts ("Advanced analysis"),
             or the user's own uploaded chart alone ("AI only"); the paired chart for a two-chart strategy (W2); the cache,
             the user's own daily cost cap and the usage row (W4);
- `routes`   GET /api/chart-reader/strategies, POST /api/chart-reader/read, GET/PUT /api/admin/chart-reader/settings.

Read-only: nothing here places, blocks or changes an order (ADR-0006). Behind the `chart_reader` flag (OFF).
"""
FLAG = "chart_reader"

# Universal scanner specification (amw.scanner.v1)

One validated contract powers templates, the natural-language builder, the JSON editor, code scanners (Strategy Lab),
runs, point-in-time replay, version comparison and backtests.

* **Envelope** (`src/scanner/spec.py::Envelope`): market (IN), exchange (NSE), universe, instrument filters, timeframe,
  higher timeframes (must be higher), evaluation frequency (manual / daily at HH:MM / interval), intraday session,
  incomplete-candle policy (always `exclude`), data-quality requirement (OK only / OK or STALE), signal expiry in bars,
  alert preferences, informational risk settings. Stored under `spec["scanner"]` of a Strategy Lab strategy.
* **Logic**: the shared IR (`src/lab/ir.py`): AND / OR / NOT, nested groups, numeric comparisons, equality (relative
  tolerance), cross over/under, arithmetic, indicators (EMA, SMA, RSI, ATR, highest, lowest, change, stdev), history
  references and predicates. Predicates: trend, higher-timeframe trend and EMA, BOS, CHoCH, last swing label
  (HH/HL/LH/LL), close above/below the last swing, near support/resistance, S/R role reversal, demand/supply zone,
  breakout retest, pullback/rally to EMA, candle pattern (13 patterns, optional next-bar confirmation), Supertrend
  direction, percentage change, RSI divergence, recent gap, session window, liquidity (average traded value), data
  quality. Unknown predicates, unknown arguments, wrong types and unsupported values are validation errors.
* **Outcomes**: every condition is PASS / FAIL / UNKNOWN. A side qualifies only if all its rules PASS; UNKNOWN never
  qualifies (fail-closed).
* **Versions**: saving publishes an immutable version (sha256 of rules + spec). Drafts never execute.
* **Replay** (`src/scanner/replay.py`): history cut at `as_of`, the bar still forming at `as_of` dropped, data quality
  re-assessed at `as_of`, pinned version evaluated. Retrospective outcomes are reported separately. Limitation: provider
  history may contain later revisions or adjustments (no point-in-time vendor snapshots).
* **Version comparison**: same symbols (<= 10), timeframe, validated data, window, backtest engine and costs. Reports
  trades, win rate, expectancy and profit factor in R, evaluation errors, runtime, data-quality failures. No winner is
  declared.
* **Natural language** (`src/scanner/nl.py`): deterministic, works without an AI key. Each clause is mapped to the
  registry; interpretations and assumptions are listed (e.g. "RSI divergence" = RSI(14), confirmed pivots k=3, last two
  pivots, latest within 15 bars); unmatched clauses are reported as UNSUPPORTED, never replaced. Primary timeframe = the
  finest timeframe mentioned; coarser trend/EMA conditions become higher-timeframe conditions.
* **Templates** (`src/scanner/templates.py`): 19 templates in Price Action, Trend, Volume, Reversal and Research; each
  lists timeframes, data requirements, limitations and "NOT VALIDATED".
* **API**: `GET /api/v1/scanner/templates`, `GET /scanner/templates/{id}`, `POST /scanner/nl`, `POST /scanners`,
  `POST /scanners/{id}/duplicate`, `POST /scanners/{id}/replay`, `POST /scanners/{id}/compare`. Lab endpoints run scans
  and backtests of a scanner version.
* **Scheduling**: schedule kind `scanner` (Watchlist & Alerts → Schedules, or `POST /api/v1/schedules` with
  `kind=scanner, scanner_id`). Each slot runs exactly once even with dashboard + API running (atomic claim).
* **Sharing**: `POST /api/v1/scanners/{id}/share?to_username=` sends a copy of the rules (no signals/data).
* **Not yet**: workspace/team sharing, drag-and-drop editing (structured editing via templates/NL/JSON + the Strategy
  Lab visual builder).

# Z10 Launch hardening (design note)

Spec: `docs/specs/ATP_PHASE0_FREEZE.md` Z10 and the §5 exit criteria. Z10 is the U3 subset: G1, G2, G4, G5, G6, G7.
It also covers the 500-session load test, staged rollout (50 → 200 → 500), the DPDP / terms / risk-disclosure pages
and a support bot.

The U3 gates have no spec file of their own in `docs/`. The gate names come from the freeze and from where other specs
cite them:
- G2 is "closed candles, no look-ahead" (`ATP_BREAKOUT_SCREENERS_ADDENDUM.md`);
- G4 is the §1.5 targets in `ATP_SCREENER_SPEC.md`;
- G5 is "stale data ⇒ alerts suppressed" (`BETA_LAUNCH_SPEC.md`, `BT_BETA_DATA_TIERS.md`);
- G7 is "status page, on-call runbooks" (`BETA_LAUNCH_SPEC.md`).

This note uses those meanings. OPEN_QUESTIONS Z10-1 asks for the U3 text.

Rules for every PR below:
- flags are off by default;
- exits are never blocked (ADR-0004);
- PAPER only, and no order path is added;
- English only, with no investment-advice wording;
- the session never touches the VPS, `.env`, secrets or LIVE.

## Split into PRs

| PR | Gate | What | Builds on (nothing rebuilt) |
|----|------|------|-----------------------------|
| **Z10-a** | G2 + G1 | A generic no-look-ahead harness, with every shipped indicator, pattern, ScreenQL function and zone detector registered in it. Golden correctness tests compare the shipped indicators with reference formulas, and chart ⇄ engine parity. Look-ahead bugs found are fixed. | `app/indicators`, `app/strategy_engine/declarative.Operand`, `app/screener/{registry,runtime,validator}`, `app/price_action/*`, `app/price_action/zones/*`; the existing per-module truncation tests stay. |
| **Z10-b** | G5 | Stale data ⇒ zero alerts, end to end. The bar-close engine and the dispatcher check `alerts_suppressed` (the BT1 badge) before they record or send. Outbox reliability: a dead-letter state after `MAX_ATTEMPTS`, a claim/lease so that two workers never send the same row, an idempotency key per delivery, and replay of dead letters by an admin. Chaos tests cover a channel down, a worker killed mid-send and a duplicate cycle. | `app/alerts/barclose.py` (skips stale bars today), `app/alerts/dispatcher.py` (outbox with retries/backoff, ADR-0022), `app/market_data/freshness.py` + `entitlement.py` (BT1 `stale` / `alerts_suppressed`), metric `market_data_stale`. |
| **Z10-c** | G6 | A tenancy and security sweep. A table-driven test sends every `/api/*` route through a second tenant's token and expects 403/404. Scope checks on every mutating route. **"No API order path reachable"** has two guards. Static: a test fails if any route or worker path not on an allow-list can reach `BrokerAdapter.place_order` / `modify_order`. Runtime: with Phase 0 lock flag `phase0_paper_only` on, a refusal sits at the single broker-order chokepoint (`brokers/rate_budget.py` wrapper). It refuses new LIVE entries only; exits of an existing LIVE position still pass (ADR-0004). | `tests/test_multi_tenancy.py`, `tests/test_security.py`, `app/auth` scopes (ADR-0009), `platform/controls.entry_blocks`, the `live_trading` kill flag (ON by default today, see Z10-3). |
| **Z10-d** | G4 + load | Perf targets as a CI perf job at small N. EOD screen: 2,500 symbols × 50 factors < 3 s, cached < 200 ms. Bar-close scan: about 200 symbols < 1 s. These run on synthetic bars, with budgets scaled by a CI factor that is printed. **Load test**: extend `backend/scripts/loadtest.py` (the repo's own httpx tool; k6/locust are not needed). It gains session-shaped scenarios: login, dashboard poll, screener run, chart, proposal list. `--users 500` is documented for the owner to run against staging. CI runs `--users 20` against the test app. Never against the VPS. | `backend/scripts/loadtest.py`, `docs/PERFORMANCE.md`, `tests/test_realism_speed.py`, `tests/test_realism_benchmark.py`. |
| **Z10-e** | G7 + rollout | **Staged rollout**: the cohort cap is a stage, `BETA_COHORT_STAGE` ∈ {50, 200, 500} (owner-set, default 50), and the effective cap is min(stage, `BETA_COHORT_CAP`). The admin sees the stage and the headroom. Moving up a stage is audited. **Status page**: a public read-only `/status` with API, worker, data feed (EOD date, Tier A sessions), alerts outbox lag and the incident banner, built from existing health probes and incidents. **Runbooks**: one page per alert (data stale, worker stale, outbox backlog, broker down, rollback) in `docs/OPERATIONS.md`. | `app/beta/invites.py` (PW6 gate, `beta_cohort_gate` flag, `BETA_COHORT_CAP`), `app/platform/controls.py` flags, `app/observability/routes.py` (`/api/system/health/deep`, worker status), `app/incidents`, `deploy/hostinger/status.sh`, `docs/OPERATIONS.md`. |
| **Z10-f** | legal + support | **Pages**: Privacy (DPDP 2023: purpose, data held, retention, rights, grievance officer contact, breach notice), Terms of use, Risk disclosure (PAPER only, not investment advice, no research-analyst service). They are public routes, and acceptance is recorded at sign-up (version + time). The legal text is editable content (`frontend/src/legal/*.md`, versioned), with a header "draft — to be reviewed by a lawyer before launch". **Support bot**: FAQ / retrieval over `docs/` user pages and the legal pages. It answers how-to questions only, runs through the existing advice/guarantee wording filter, and has no tools that read positions or touch orders. Above a confidence floor it hands off to the feedback form. Flag `support_bot`, off. | `app/retention` (DPDP retention policy), `app/compliance` (`in_sebi.json` DPO / breach items), `app/ai` providers + the H-C1 c advice filter + `/api/ai` rate limit, PW6 feedback form. |

Z10-a is first: it adds no runtime surface, and the screens that Z7 is building can register in its harness. Then
Z10-b, Z10-c (they back the §5 criteria "no API order path reachable" and "stale data ⇒ alerts suppressed"), Z10-d,
Z10-e and Z10-f. "Rollback rehearsed" belongs to Z11, which owns the deploy bundle. Z10-e's runbook links to it.

## Z10-a in detail

**Harness** (`backend/app/quality/lookahead.py`). A case is registered with `register(name, fn, kind=...)`:
- `series`: `fn(bars)` returns one value per bar (a Series, DataFrame, ndarray, or a tuple/dict of them).
- `events`: `fn(bars)` returns records, each with the bar index at which it became known (`known_at`, e.g. a swing's
  confirmation bar).
- `last_bar`: `fn(bars)` answers only for the last bar (ScreenQL's structure and option filters). These are causal by
  construction: they only ever get the prefix. The harness checks that they are deterministic and leave their input
  alone. The validator stops them from being read as a history (see below).

For each sampled bar t, the harness checks:
1. **truncation**: `fn(bars[:t+1])` equals `fn(bars)` up to t (for events: the records known by t);
2. **future mutation**: the bars after t are replaced by an up-spike, a down-spike and an independent random walk. The
   output up to t must not change. This catches windows centred on the bar, a min/max over the whole frame, and
   normalisation by statistics of the full series.
3. **immutability**: the input frame is unchanged after the call.

Bars come from `synthetic_bars(...)`. These are deterministic and session-shaped (NSE 09:15–15:30 IST, UTC index),
with overnight gaps, flat bars and zero-volume bars, so session functions (VWAP, PDH, opening range) and the
higher-timeframe alignment are exercised too.

**Registry and coverage** (`backend/app/quality/causal_cases.py`). This file registers:
- every `app.indicators` function;
- every `declarative.Operand` indicator, on the base timeframe and on a higher one;
- every candlestick pattern;
- the causal swings;
- the market-structure events;
- the zone detectors;
- **every ScreenQL function** in `registry.FUNCTIONS`, through `runtime.evaluate` on base and `@` higher timeframes.

A coverage test fails when a new registry function, indicator name or pattern has no case. The only exemptions are
named in the file with a reason (functions that read no bars: reference data and the option chain).

**G1 golden tests** (`backend/tests/test_z10a_indicator_golden.py`). The references are plain loops, written from the
textbook formula and independent of pandas:
- SMA, EMA, Wilder RMA, RSI, TR / ATR, ADX / ±DI, Supertrend, Bollinger (sample sd), MACD;
- session VWAP (IST day, TWAP for symbols without volume), day open, PDH / PDL / PDC, opening range.

They run on generated bars (no large fixture file). Hand-checked small cases pin the exact numbers. Each of the 14
candlestick patterns has a canonical shape that fires and a near miss that does not. One check documents the Wilder
seed: ATP seeds with the first value (pandas `ewm(adjust=False)`), where TA-Lib and TradingView use the SMA of the
first n. The two agree to within 1e-6 after 10 × n bars (OPEN_QUESTIONS Z10-2).

**Chart ⇄ engine parity.** The Pro Chart re-implements the indicators in TypeScript (`frontend/src/utils/indicators.ts`)
"to match the backend". A small fixture generated by the backend (`frontend/src/utils/__fixtures__/indicator_parity.json`,
checked against the backend by a pytest) is compared in a vitest.

## Not in Z10

- Full U3 gates (N3): see "U3 full gates" below.
- New indicators.
- Changing any indicator's definition. Where the harness or a golden test finds a mismatch, the fix makes the
  code match its documented formula; a definition change goes to OPEN_QUESTIONS instead.

## U3 full gates (N3)

The U3 gates still have no spec text (Z10-1). U3 gathers the screener spec's own requirements into one manifest,
`backend/app/quality/gates.py`, with the tests that enforce each row:
- G1-G7 (the freeze's names);
- SCREENER_SPEC §1.5 (performance), §5 (quality, observability, reproducibility, pitfalls) and the §7 acceptance rows (ACC);
- the evidence addendum's per-entry definition of done (DoD).

`python -m app.quality.gates` prints the report.

- **Rows.** Each row names its tests as `path::test` (a pytest function, or a vitest title).
  `tests/test_u3_gates.py` fails when a named test does not exist.
- **Gaps.** A row that is not met declares a gap, which must be an OPEN_QUESTIONS id (U3-2 … U3-7).
- **Measured rows.** A measured row fails once its target is met while its gap is still declared. The count of
  pre-built screens is 23 of 50.
- **G3 (provisional, U3-1)** is §5 "Reproducibility" plus §7 "every number traceable to a data timestamp".
  - A run now stores its AST (checked against `ast_sha256`), its parameters and exchange, `data_as_of` (the newest bar
    close it read) and each symbol's last bar. Migration `d7f1a3c5e9b2` adds five nullable columns (ROLLBACK_SAFE).
  - `POST /api/screener/runs/{id}/replay` (flag `screener_run_replay`, off) re-fetches the run's symbols through the
    caller's own source and keeps only the bars closed by `data_as_of`. It evaluates the same AST and parameters and
    reports each symbol as reproduced, a different last bar, or the same last bar with a different answer (a correction).
  - A replay stores nothing. Runs stored before U3 answer 409.
- **DoD.** `definition_of_done()` checks every registry entry: a doc string, a flag in DEFAULT_OFF_FLAGS, and a Z10-a
  causal case or a named exemption. Once EV3 puts `evidence.DRAWN_AS` on main, it also checks the drawn-as
  declaration (U3-6). Runtime fixtures stay enforced by `test_s1b_screen_runtime.py`.
- **G1 price action.** The ScreenQL `Pattern` and `SwingHigh` / `SwingLow` functions are compared bar by bar with the
  price_action engine on the frozen 15m fixture (BANKNIFTY-like synthetic data, SC-12).
- **G7 metrics.** Two new metrics:
  - `atp_screen_run_duration_seconds{path=run|preview|barclose}`;
  - `atp_screen_cache_lookups_total{outcome=hit|miss}`.

  Ingest lag and tracing are U3-5.
- **Open rows:**
  - G2.fundamentals (U3-4, LG3);
  - G4.live_latency and G4.alert_load (U3-3);
  - G7.ingest_lag (U3-5);
  - DoD.drawn (U3-6, EV3);
  - ACC.prebuilt (U3-2);
  - ACC.fo_rrg_breadth and ACC.pitfalls (U3-7).

# N1 F: portfolio risk (F1 model and read API -> F2 entry caps -> F3 cards)

Status: **plan only**. No code until the coordinator confirms. This extends the F rows of
`docs/design/N1_EXECUTION_CORE.md` §4. Open questions are F-1 to F-7 in `docs/OPEN_QUESTIONS.md`, each with a
provisional default.

## 0. Rules (unchanged from N1)

- PAPER only. F reads books and refuses **entries**. It never places, modifies or exits an order. The Z10-c graph
  must show no F function in `reaching()`.
- Exits are never blocked (ADR-0004). No exit path consults an F cap; a call-graph test enforces this.
- BT1b: the book is the user's own (`trades.user_id`), valued at its own marks. No broker session is read, and
  nothing of one user is shown to, or used for, another.
- Every F flag goes in `DEFAULT_OFF_FLAGS`. Every new route goes in `tests/tenancy_route_table.py`. No migration is
  planned (see F2).

## 1. What exists, and the gaps F closes

| Piece | Where | Gap |
|---|---|---|
| R4 open risk at the stops by correlation bucket (all indices in one `INDEX` bucket, else the name), cap `max_portfolio_risk_pct` | `risk_engine/guardian.py` (`open_risk_by_bucket`, `portfolio_risk_block`) | **tenant-wide**: it sums every user's trades, not the user's own book (BT1b). Buckets are names only, with no sectors. It runs after sizing in the pipelines, not before a proposal. |
| Portfolio snapshot: gross / net notional, per symbol and strategy, concentration, open risk at the stops | `portfolio/engine.py`, `GET /api/portfolio/exposure` | tenant-wide book. No sectors, no beta, no correlated view. |
| P1-d risk by default (risk cap, daily loss lockout, entries per day), refusals before a proposal | `risk_engine/by_default.py` (`proposal_refusal`), `execution/approval.py:205` | no portfolio-level rule |
| U1-b NSE sector classification (as-of, by ISIN) | `universe/indices.py` (`classification_on`) | not used for risk |
| Shared lake Daily bars (EOD sources), including `NSE_INDEX:<code>` index bars | `market_lake/asof.py`, `market_lake/bhavcopy.py` | no beta |

F does not change R4. R4 stays the tenant-level backstop that runs after sizing. F adds the **per-user** view and
caps on top of it (F-1).

## 2. Trade sources (read at Trade `origin/main` a6f0800fe3cf9df59d02b17f75f8bb857b7ae6d9)

Trade has no portfolio-risk module, only three small rules spread through its engine and DB layer. F ports their
**rules**, not their code, because the code is SQLite, Streamlit and a Upstox funds call. Parity is tested with
hand-built cases that mirror each rule; there are no golden files. Three rows go into `TRADE_PORT_MAP.md`:

| Trade | Rule | ATP |
|---|---|---|
| `trading_engine.check_portfolio_risk_cap` + `database.get_open_live_max_loss_total` | Open max-loss of a correlated bucket + the new trade's max loss <= cap % of capital. Buckets: index options NIFTY + BANKNIFTY + SENSEX together (cap 6 %), all MCX together (6 %). Other symbols are not capped. Default **off**. With the capital unknown it **refuses** (fail closed). Applies to LIVE and PAPER; shadow trades are excluded. | F2 `max_correlated_risk_pct`, on the user's own book. ATP's `INDEX` bucket already holds all indices, and F adds sector buckets. Capital comes from the PAPER account (`by_default.capital_basis`); with no basis the cap refuses, as in Trade. MCX is not in ATP: no row. |
| `trading_engine` `max_open_positions` (MCX kill switch, default 2) | At or above N open positions, no new entry. | F2 `max_open_positions` for any symbol; a multi-leg group counts as one position. |
| `database.compute_portfolio_risk_summary` | Total max loss, credit, MTM, and counts by direction. At 2 or more directional positions that are all bullish or all bearish, it warns about one-sided, correlated risk. | F1 `direction` block and the same warning (text only, never a refusal). |
| `database.compute_portfolio_greeks` | Option Greeks of the open book | not in F (options Greeks need the chain; later, with the options builder). |

## 3. F1: the model (pure) and `GET /api/portfolio/risk`

**`app/portfolio/risk.py`** is pure: positions, prices, classification, betas and capital in; a `RiskView` out. No
I/O, so the API, the caps (F2) and the cards (F3) share one implementation, as `portfolio/engine.py` does.

- **Book:** the caller's open PAPER trades (`tenant_id`, `user_id`, `mode` PAPER, `exit_time` NULL). A multi-leg
  group counts once, at its structure's risk (the R4 rule, reused from `guardian`).
- **Exposure:** gross and net notional, long and short, and % of capital.
- **Open risk at the stops:** in total and by bucket.
- **Buckets:**
  - Indices and index derivatives go to `INDEX` (as R4).
  - A stock, or a stock's F&O by its underlying, goes to its NSE sector on the day (U1-b `classification_on` via
    the ISIN).
  - Anything without a classification goes to `UNCLASSIFIED:<name>`, so it is never pooled with others by
    accident (F-3).
- **Beta to NIFTY 50:**
  - Computed from the lake's Daily closes (EOD sources only, `nse_bhav:cm` for stocks, `NSE_INDEX:NIFTY50` for the index).
  - Window: 250 sessions known at yesterday's close; needs at least 120 shared sessions.
  - Fewer sessions means beta is "unknown": shown as 1.0, flagged, and never hidden (F-4).
  - Index futures or options on NIFTY have beta 1 by definition; other indices use their own index series.
  - Computed once per symbol per IST day, in memory.
- **Beta-weighted exposure:** net and gross, in NIFTY-equivalent rupees.
- **Direction:** counts by side, and the one-sided warning from Trade's rule.
- **Prices** (as built in F1): each trade's own `mark_price`, which the worker keeps on open PAPER trades; with no
  mark, the entry price, flagged per symbol (`priced_at_entry`). No broker session is read, so the view needs no
  live-data entitlement and cannot use another user's session. This replaces the planned live quotes: the caps
  compare risk at the stops, which does not depend on the price.

**Route:** `GET /api/portfolio/risk` returns the caller's own book only. It is a member route in the tenancy table
and answers 503 when `portfolio_risk` is off. It is read-only, reaches no sink, and leaves the Z10-c allow-lists
unchanged.

## 4. F2: caps as entry refusals only (`portfolio_risk_caps`, needs `portfolio_risk`)

**Settings.** Three new keys in the user's P1-d risk settings, stored in the same JSON (`RiskSettings`, so no
migration). Each gets bounds in `BOUNDS`, a platform ceiling in `controls.risk_ceilings`, and the P1-d rule that
loosening waits until the next day (`loosens`):

| Key | Meaning | Default |
|---|---|---|
| `max_sector_pct` | Notional in one bucket, as % of capital, after this entry | 0 (no limit) |
| `max_correlated_risk_pct` | Open risk at the stops in this entry's bucket, plus this entry's max loss, as % of capital (Trade's rule, per bucket) | 0 (no limit) |
| `max_open_positions` | Open positions (a group counts once) | 0 (no limit) |

All three default to 0 = no limit (F-5). With the flag on, nothing changes until the user sets a limit.

**Where caps are checked.** One function, `portfolio.caps.entry_refusals(session, user, candidate) -> List[str]`,
with candidate = symbol, side, quantity, entry and stop. It is called from two places:

1. Before a proposal, next to `proposal_refusal` in `approval.propose_entry`. The intent is refused with the
   reason, and the core records it as REFUSED. This covers X1.
2. At Approve, in `approval.execute_entry` before the re-quote, because the book may have changed since the
   proposal. A refused Approve closes the proposal unplaced with the reason, as a failed re-quote does.

Each call is a read plus a comparison. There is no call-site change in the order paths, and `guard_entry` is
unchanged (F-6).

**Never on an exit.** Exit rules, "Exit now?", kill-switch flattening and stop orders never import or call `caps`.
A Z10-c call-graph test proves that no EXIT / PROTECTIVE site's callers reach `app.portfolio.caps`, and an
ADR-0004 test closes a position whose bucket is over every cap.

**Fail mode.**
- Classification missing: the symbol is its own bucket (not refused).
- Beta unknown: caps do not use beta.
- Capital basis unknown or 0: refuse, with the reason (Trade's fail-closed rule; F-7).
- Read error: refuse, with the reason. It is an entry, so failing closed is safe.

## 5. F3: cards and lines (`portfolio_risk`)

- **Positions page:** a "Portfolio risk" card with exposure, open risk by bucket against the user's caps, beta
  exposure, and the one-sided warning. All strings go through i18n, with a fixture that is the server's own payload
  (the X1b pattern).
- **Telegram `/risk`:** three to five lines from the same `RiskView`, own book only.
- **Proposal card:** a "portfolio impact" line, e.g. "after this entry: IT 18 % of capital, open risk 2.1 %,
  positions 4 of 5". It is information, never advice.

## 6. Flags

`portfolio_risk` (F1, F3) and `portfolio_risk_caps` (F2; it acts only while `portfolio_risk` is on too). Both go
in `DEFAULT_OFF_FLAGS`. Off means 503 on the route, no card, no line and no refusal.

## 7. Tests

- **Pure model:** hand-built books.
  - Gross and net exposure, and a multi-leg group counted once.
  - Buckets: index, sector, F&O resolved to its underlying's sector, unclassified.
  - Beta on a synthetic pair with a known slope (y = 1.3x + noise gives ~1.3), "unknown" below 120 sessions, and
    no look-ahead (a bar dated today changes nothing).
  - The direction warning at exactly Trade's thresholds (2 or more directional, all one side; neutral ignored).
- **Trade-rule parity cases (F2):**
  - The bucket sum + new max loss > cap boundary (equal passes, one rupee more refuses).
  - Index options pooled across NIFTY, BANKNIFTY and SENSEX.
  - Capital unknown refuses.
  - `max_open_positions` at N-1 / N.
- **BT1b and tenancy:**
  - User A's route, caps and card never include B's trades or prices.
  - Under `byob_data_tier` no tenant session is used.
  - The route is in the tenancy table and the sweep passes.
- **Safety:**
  - Z10-c order paths are unchanged and no `app.portfolio.risk` / `caps` function is in `reaching()`.
  - The ADR-0004 exit test (above).
  - Flags-off tests: the route returns 503 and propose / Approve behave exactly as today.
- **Integration:** an X1 intent refused by a sector cap ends REFUSED with the reason, and nothing is proposed.

## 8. PR plan

| PR | Content | Size (excl. tests) |
|---|---|---|
| F1 | `portfolio/risk.py` (pure), the beta cache from lake closes, the shared BT1b price helper, `GET /api/portfolio/risk`, flag, tenancy row, port-map rows | ~700 |
| F2 | settings keys, bounds and ceilings, `portfolio/caps.py`, the two call sites (propose, Approve), the ADR-0004 and Z10-c tests | ~500 |
| F3 | the Positions card (i18n, fixture, story), Telegram `/risk`, the proposal-card line | ~700 |

Order F1 -> F2 -> F3. F2 and F3 both need F1 only.

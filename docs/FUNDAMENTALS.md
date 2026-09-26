# Fundamental Analysis & Company Intelligence Engine

A real, institutional-grade fundamental analysis module - built as a "core engines first" slice
of a much larger 49-section specification, per explicit user direction. This document explains
what's real, what's deliberately left for a human/analyst to supply, and what's still out of
scope.

## Design principle: never fabricate a number

Every other engine in this platform (strategies, backtesting, option chain) computes on real
input data - candles, option chain rows - supplied by the caller, never invented. Fundamental
data follows the exact same rule, and it matters more here: a wrong P/E or a fabricated "strong
moat" claim is worse than useless, it's actively misleading.

So:
- **Every input model carries a `SourceCitation`** (source, URL, publication date, retrieved
  date, confidence) - see `app/fundamentals/models.py`. A `CompanyProfile`, `FinancialPeriod`,
  `ShareholdingSnapshot`, `CorporateAction`, and `QualitativeFactor` are never presented without
  a trace back to where the number came from.
- **Ratios are never stored, only ever derived live** from raw statement figures (revenue,
  EBITDA, PAT, debt, ...) that were actually entered. There is exactly one place a number can be
  wrong: the cited source it was entered from.
- **Qualitative judgments never come from an engine.** Business-quality moat factors,
  management-quality ratings, and SWOT bullets are `QualitativeFactor` records a human enters
  and cites (`category="BUSINESS_QUALITY_MOAT"` etc.) - `BusinessQualityEngine` averages what's
  given and raises a clear error if nothing has been entered, rather than inventing a rating.
- **A missing component in the Fundamental Score is scored neutral (50), not guessed** - and the
  breakdown row says so explicitly (`FundamentalScoreEngine`).

## What's real and working

All of this is genuine computation, unit-tested, and verified end-to-end through the API and a
live Playwright run of the frontend (register → add company → add financials → run every
analysis tab → score → fusion → intelligence card → screener → sector rotation):

| Engine | File | What it computes |
|---|---|---|
| Revenue growth | `engines/statement_analysis.py` `RevenueAnalysisEngine` | YoY, 3Y/5Y CAGR, a sustainability read (steady vs. spiky growth) |
| Profitability | `ProfitabilityEngine` | Gross/EBITDA/EBIT/net margins, ROE/ROCE/ROA, margin trend vs. revenue growth |
| Earnings quality | `EarningsQualityEngine` | CFO/PAT ratio, other-income and exceptional-item dependence, quality label + warnings |
| Quarterly comparison | `QuarterlyComparisonEngine` | QoQ/YoY deltas across revenue/EBITDA/margin/PAT/EPS/CFO with ↑/↓/→ direction |
| Balance sheet | `BalanceSheetEngine` | Debt/equity, Net Debt/EBITDA, interest coverage, current/quick ratio, debt & liquidity risk |
| Cash flow | `CashFlowEngine` | CFO, FCF, FCF yield, the "PAT up but cash flow lagging" red flag |
| Valuation | `engines/valuation.py` `ValuationEngine` | P/E, P/B, EV/EBITDA, EV/Sales, PEG, dividend/FCF yield, classified against supplied historical/peer P/E |
| DCF | `DCFEngine` | A real FCFF discounted cash flow (revenue → EBITDA → NOPAT → FCF, discounted at WACC, Gordon-growth terminal value) with bull/base/bear sensitivity |
| Business quality | `engines/business_quality.py` | Weighted average of cited moat-factor scores |
| Red flags | `engines/red_flags.py` | Aggregates earnings-quality/balance-sheet/cash-flow/shareholding/corporate-action signals into one list |
| SWOT | `engines/swot.py` | Combines cited qualitative bullets with computed signals (margin trend, debt risk, earnings quality) |
| Scenario | `engines/scenario.py` | Bull/Base/Bear one-year revenue/EBITDA/PAT/EPS projection off the latest period |
| Fundamental Score | `engines/score.py` `FundamentalScoreEngine` | The exact weighted composite (Business Quality 15%, Earnings Quality 15%, Growth 15%, Profitability 10%, Balance Sheet 10%, Cash Flow 10%, Management 10%, Sector Outlook 5%, Valuation 5%, Macro/Event Risk 5%) → 0-100, graded Exceptional/Strong/Good/Average/Weak/Poor |
| Fusion | `FusionEngine` | Combines the Fundamental Score with this platform's existing technical Signal Scoring Engine into a Final Composite Score and one of A1 LONG BIAS / A1 SHORT BIAS / WATCHLIST / CAUTION / NO TRADE, per the 2×2 decision matrix (technical strong/weak × fundamental strong/weak) |
| Peer comparison | `engines/peer_comparison.py` `PeerComparisonEngine` | Ranks companies entered under the same sector by ROCE (falling back to EBITDA margin), skipping any company with no financial periods rather than showing fabricated zeros |
| Pre-earnings analysis | `engines/pre_earnings.py` `PreEarningsEngine` | Synthesizes revenue/margin trend and red-flag severity into a Bullish/Neutral/Bearish bias and risk level ahead of a company's nearest upcoming RESULTS calendar event - never a fabricated "Street expectation" |
| Post-earnings analysis | `engines/post_earnings.py` `PostEarningsEngine` | Compares the just-reported actual revenue/PAT against this company's own historical growth trend (average YoY growth of the periods before it) to produce a beat/inline/miss verdict - "expected" is always the company's own trend, never an external analyst consensus |
| Sector-specific fundamentals | `engines/sector_specific.py` `SectorSpecificEngine` | Classifies user-entered sector KPIs (banking NIM/CASA/GNPA/NNPA/PCR/CRAR, IT utilization/attrition/TCV growth/revenue-per-employee, auto volume growth/inventory days/export mix, pharma US-generics mix/R&D%/USFDA observations, oil & gas GRM/refining utilization, cement capacity utilization/realization) against published-style thresholds - `sector_key` is explicit, never inferred from free-text sector/industry fields |
| Event Impact Score | `engines/event_impact.py` `EventImpactEngine` | A -100..+100 net directional score from `CorporateAction.expected_*_impact` fields already on record, weighted toward more recent events |
| Fundamental Alert Engine | `engines/alerts.py` `AlertEngine` | Re-packages high/extreme red flags, an extremely-expensive valuation, a DCF overvaluation >20%, an imminent (≤7 day) RESULTS event, and strongly negative event momentum into one prioritized alert feed - computes no new judgement itself |

Two more real, non-fabricated pieces sit alongside these engines:

- **Earnings calendar** (`EarningsCalendarEvent` model, `GET/POST /api/fundamentals/companies/{symbol}/calendar`,
  `GET /api/fundamentals/calendar/upcoming`) - a company's own scheduled events (results, AGM, board
  meeting, dividend, bonus, split, buyback, record date, investor day, product launch, regulatory
  decision), each user-entered and citable, never invented. The "upcoming" endpoint aggregates
  every company's calendar into one soonest-first feed.
- **Peer comparison endpoint** (`GET /api/fundamentals/sectors/{sector}/peers`) - wires
  `PeerComparisonEngine` to every company persisted under a given sector.
- **Pre-earnings endpoint** (`GET /api/fundamentals/companies/{symbol}/analysis/pre-earnings`) -
  anchors `PreEarningsEngine` to the nearest upcoming RESULTS event on that company's calendar;
  404s if none has been entered, since there's nothing to be "pre-" of otherwise.
- **Post-earnings endpoint** (`GET /api/fundamentals/companies/{symbol}/analysis/post-earnings`) -
  anchors `PostEarningsEngine` to the most recent *past* RESULTS event; 404s if none has been
  entered, 422s if fewer than 3 financial periods exist (a trend needs at least two periods
  before the just-reported one).
- **Sector metrics CRUD** (`GET/POST /api/fundamentals/companies/{symbol}/sector-metrics`,
  `GET /api/fundamentals/sector-metrics/specs` for the known sector keys/metric codes/units) and
  the analysis endpoint (`POST /api/fundamentals/companies/{symbol}/analysis/sector-specific?sector_key=...`).
- **Event impact endpoint** (`GET /api/fundamentals/companies/{symbol}/analysis/event-impact`).
- **Alerts feed** (`GET /api/fundamentals/companies/{symbol}/alerts`) - red flags plus imminent-earnings
  and negative-event-momentum checks; skips valuation/DCF-derived alerts since those need a market
  price that isn't persisted.
- **Final Company Report** (`GET /api/fundamentals/companies/{symbol}/report`) - the spec's closing
  section, aggregating the Intelligence Card, SWOT, red flags, alerts, and event impact into one
  document. It computes nothing new of its own, so it inherits every other engine's never-fabricate
  guarantee; valuation/DCF are left out rather than guessed, since both need a market price.

All company/financial/shareholding/corporate-action/qualitative-factor data is **shared
reference data** (like an instrument master), not user-private: reads are open to everyone,
writes require auth so every contribution is attributed (`created_by`).

## What's a data-entry tool, not a live feed

There is no SEBI/NSE/BSE/commercial-vendor credential wired into this platform, so the module
cannot *pull* live fundamental data on its own yet - the user explicitly asked for primary/
official sources to be prioritized, but confirmed no API access exists, and asked for this to
be a real, non-fabricated engine over data entered through the API (the same pattern this
platform already uses for OHLCV candles: no live broker, so a clearly-labeled real data path
exists instead of a fabricated one).

`app/fundamentals/providers/nse.py` is a real, best-effort `NSEProvider` for NSE India's public
JSON API (the same endpoints community tools like `nsepython`/`jugaad-data` use) - written
following the exact `BrokerInterface` adapter pattern already established for brokers, and its
parsing logic is verified against realistic mocked HTTP responses
(`tests/test_nse_provider.py`). **Its live network behavior is unverified**: the development
sandbox's network policy blocks `nseindia.com` entirely (confirmed via a direct 403 policy
denial). Smoke-test it against the real site, and expect to adjust endpoint paths/headers, before
relying on it - NSE does not publish a stable, versioned API contract.

## What's still out of scope (deliberately deferred, not silently dropped)

Per the original 49-section brief, the pieces that fundamentally require a live external feed
this platform doesn't have credentials for were not built as "real" engines, because doing so
honestly would mean they do nothing without a subscription:

- **National/international event impact engines** (Union Budget, RBI policy, Fed decisions,
  crude oil/currency transmission) - these need a live macro/news feed.
- **Earnings call transcript NLP / management guidance extraction** - needs either a transcript
  feed or manual entry; `QualitativeFactor` supports manual entry of a guidance-credibility
  score today, but there's no automatic extraction.
- **Market-wide/NIFTY-level fundamental engine** - the sector rotation endpoint
  (`GET /api/fundamentals/sectors`) and the peer comparison endpoint
  (`GET /api/fundamentals/sectors/{sector}/peers`) only rank companies actually entered into
  this platform, not the full NSE/BSE universe for that sector.
- **Automatic earnings-surprise detection against analyst consensus** - no consensus-estimate
  feed exists; the closest equivalent today is comparing entered periods QoQ/YoY, or the
  pre-earnings bias/risk read against a company's own trend.

These are natural next steps once a real data feed (a paid vendor, or a working NSE/BSE
integration verified outside this sandbox) is connected - the engines and data model above are
built to consume that data the moment it's available, without redesigning anything.

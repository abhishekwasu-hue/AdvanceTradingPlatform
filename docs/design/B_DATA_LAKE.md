# Part B - Market data lake: design note

Spec: MASTER SPEC v1 §3 (B0-B7) with v1.1 seams (ADR-0016) and v1.2 global-first (ADR-0017). Status: **design**.
Decisions: ADR-0013 (store), ADR-0016 (provider seams), ADR-0017 (global-first) - provisional until the owner answers.

## Status
| # | Item | Status | PR |
|---|---|---|---|
| B0 | ADR-0013 (+0016, 0017) | ✅ drafts (provisional) | this PR |
| B1 | Schema: candles, ticks, option chains, instrument_master_versions, corporate_actions, data_quality_events (as-of) | planned | 2 |
| B2 | Ingest: tick writer from stream.py, candle builder (label = bar END), vendor adapter interface + one vendor, broker backfill, bhavcopy job | planned | 3-4 |
| B3 | Quality: gap/spike/duplicate/late, cross-source mismatch, survivorship, universe-as-of | planned | 5 |
| B4 | Adjustment: corporate-action factors, adjusted/unadjusted views (none for F&O) | planned | 5 |
| B5 | API `/api/market-data/history(..., as_of, adjusted)`; backtests read it; client-posted candles deprecated | planned | 6 |
| B6 | Retention tiers, compression, metrics, alerts | planned | 6 |
| B7 | Tests: bar-close labels, as-of, adjustment math, quality detectors, mocked vendor | with each PR | - |

## Test plan (per PR)
- As-of: a row ingested after T is invisible to `as_of=T`; a correction (version 2) replaces version 1 only for
  `as_of` after the correction.
- Bar close: a 1-min bar built from ticks is labelled by its END and published only after it closes (same rule as
  realism C1).
- Adjustment: a 1:2 split halves prices and doubles volume before the ex-date in the adjusted view only.
- Quality: crafted gaps, spikes, duplicates and late bars each raise exactly one event.
- Vendor adapter: contract suite on a recorded transport (ADR-0016).
- Migration: upgrade + downgrade on Postgres with the Timescale extension.

Open questions: OPEN_QUESTIONS B-1 (Timescale image on the Hostinger host before go-live, or after), B-2 (vendor
choice and cost - over $5/month needs the owner).

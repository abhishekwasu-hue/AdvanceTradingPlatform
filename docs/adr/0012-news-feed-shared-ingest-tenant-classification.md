# ADR-0012: News feed - shared ingest, classification with the organisation's own key

**Date:** 2026-10 · **Status:** accepted · **Phase:** BB

## Context
The Copilot and the monitoring agent need to know about market-moving news (RBI decisions, SEBI
circulars, macro shocks) without a person typing it in. The platform rules are: no scraping, each
source's terms respected, AI provider keys belong to the organisation and are entered in Settings
(never in the environment), AI never places or changes an order, exits are never blocked.

## Decision
1. **Ingest is shared.** The worker fetches each enabled public feed once per cadence for every
   organisation and stores items as `news_events` rows with `origin=FEED`, `verified=false`, the item
   URL and a dedupe hash. Only official feeds (RBI, SEBI) are on by default; exchange and publisher
   feeds are registered but off until the operator confirms their terms (`docs/DATA_SOURCES.md`).
2. **Keyword classification is shared and free**; it drives alerts (severity >= 4) but never a trading
   proposal on its own.
3. **AI classification is per organisation, with that organisation's own provider key**, batched
   (<= 20 headlines, `<untrusted_data>` block, strict schema parse), cached per (organisation, item),
   metered. Two organisations therefore pay twice for the same headline; this is accepted today
   because it keeps every external call attributable to the key holder and no organisation's paid
   compute is shared with another. **At scale the alternative is a platform-level classification run
   once with an operator key and shared as reference data**; that would need a platform AI key
   (operator secret) and a change to this record, and is deferred until there is more than a handful
   of organisations.
4. **Proposals need corroboration**: the organisation's own AI reading at severity >= 4, or two
   different sources reporting the same event. One proposal per event per organisation; the human
   approves; PAUSE only at severity 5 and only for new entries.
5. Feed rows age out with the retention policy (`RETENTION_NEWS_FEED_DAYS`, default 365); a person's
   cited MANUAL entry never does.

## Consequences
- No new table for the shared rows (the existing `news_events` is already platform-wide); one new
  tenant-scoped table for the AI classifications.
- The Copilot's briefing and the thesis (Phase BD) read the same rows; nothing is fetched twice.
- Turning a feed on is an audited operator action, so the terms decision is traceable.

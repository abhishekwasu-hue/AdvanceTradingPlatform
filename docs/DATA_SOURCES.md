# Data sources and their terms

What the platform reads from outside the tenant's own broker session, where it comes from, what the
publisher's terms allow, and what is stored. Nothing here is scraped from a web page; every entry is
either a published syndication feed, a documented public API, or the tenant's own broker. A source
whose terms are unclear is **off** until the operator confirms it (Admin, or the News & Events page).

| Source | Used by | Terms (one line) | Stored | Default |
|---|---|---|---|---|
| Tenant's broker (Upstox, Zerodha, ...) | candles, quotes, option chains, orders | the tenant's own API agreement; keys in Settings, encrypted | candles cached 60 s; snapshots in market memory | on |
| Upstox public instrument master | contracts, lot sizes | public download published by Upstox for API users | `instruments` table | on |
| Yahoo Finance chart endpoint / Stooq daily CSV | Phase AU global cues | free, unofficial, delayed about 15 min, can change without notice; background only, never a signal input | one quote row per market in market memory | `GLOBAL_CUES_ENABLED` |
| **RBI press releases RSS** (`rbi.org.in/pressreleases_rss.xml`) | Phase BB news feed | official feed RBI publishes for syndication | headline, link, time (`news_events`, origin FEED, unverified) | on |
| **SEBI RSS** (`sebi.gov.in/sebirss.xml`) | Phase BB news feed | official feed SEBI publishes | headline, link, time | on |
| NSE corporate announcements RSS | Phase BB news feed | NSE's website terms restrict automated access and redistribution of content; the RSS exists but its permitted use is not stated | headline, link, time | **off** |
| BSE corporate announcements XML | Phase BB news feed | BSE's terms restrict automated access and redistribution | headline, link, time | **off** |
| Economic Times - Markets RSS | Phase BB news feed | publisher RSS offered for personal, non-commercial syndication; no body stored, link back to the publisher | headline, link, time | **off** |
| Moneycontrol - Market reports RSS | Phase BB news feed | publisher RSS; no body stored, link back | headline, link, time | **off** |
| Paid news provider | Phase BB seam (`NewsProvider`, not implemented) | per contract | - | off |
| NSE daily FII/DII activity page | Phase BC sentiment (`fii_dii` component) | NSE website terms restrict automated access; no documented API terms | - | **off** (`FII_DII_SOURCE` unset) |
| NSDL FPI monitor | Phase BC alternative for FII flows | public statistics, monthly/fortnightly cadence - too slow for a daily score | - | not wired |
| Tenant's broker option chain and heavyweight quotes | Phase BC sentiment (PCR/OI, breadth) | the tenant's own API agreement | SENTIMENT snapshot (score, components) in market memory | on with a broker session |

## Phase BB rules

* One fetch serves every organisation (shared `news_events` rows, `origin=FEED`, `verified=false`). The
  publisher's summary text is read in memory for the keyword classification and discarded; only the
  headline, the link and the publication time are kept, for `RETENTION_NEWS_FEED_DAYS` (default 365).
* Fetch cadence: every 15 minutes while the worker runs; every 5 minutes within 60 minutes of a global
  macro event on the Risk Guardian calendar (`market_events` with no tenant: RBI MPC, Union Budget, FOMC,
  CPI - the operator adds them on the Risk page with "global event").
* The XML reader caps the body at 512 KB and refuses documents that declare a DOCTYPE or entities.
* AI classification uses the organisation's own provider key (Settings), one batched call for up to 20
  headlines, the headlines inside an `<untrusted_data>` block; the result is cached per organisation and
  item. No key: the keyword classification applies. Metered as `ai_news_classify`.
* A keyword-only severity 4 or 5 raises a `NEWS_ALERT` notification, never a trading proposal. A
  monitoring-agent proposal needs the organisation's AI reading at severity 4 or more, or two different
  sources reporting the same event; at most one proposal per event per organisation; a human approves it
  (ADR-0006) and it never touches an exit (ADR-0004).
* Turning a source on is an operator decision recorded in the audit log with the terms note.

Social-media sentiment is out of scope by decision (Phase BC uses deterministic market data only).

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
| The owner's own broker session (Upstox, Tier A; the Analytics Token) - option chains | TM 9.4 chain history (`md_tenant_chain_snapshots`; docs/design/CHAIN_HISTORY.md) and, per organisation through its own session, the OI collector (TM-7) | the session holder's own API agreement: recorded only through the owner's own session and read only by the owner's tenant (never pooled or shown to another user, RDY-6) | every strike (LTP, bid, ask, volume, OI, OI change, IV, the broker's Greeks), spot, time - zlib-compressed JSON per chain; kept until the owner sets `RETENTION_CHAIN_HISTORY_DAYS` | **off** (`chain_history` flag) |
| NSE archives equity lists (`EQUITY_L.csv`, `SME_EQUITY_L.csv`, `eq_etfseclist.csv`) and `symbolchange.csv` | Screener U1-a universe (`securities`, `symbol_history`) | published download files; NSE's website terms restrict automated access, so the job stays off until the operator has checked the terms; the admin's manual upload of the same files is the alternative | rows with source, fetched_at, checksum; never fabricated history | **off** (`UNIVERSE_SYNC_ENABLED`) |
| NSE Indices constituent files (`niftyindices.com/IndexConstituent/ind_*list.csv`, catalogue in `backend/app/universe/data/indices.json`) | Screener U1-b index membership and NSE sector classification | published downloads of the index provider; terms to be checked (and each file name confirmed) before the job is switched on; manual upload is the alternative | membership ranges, classification ranges, source/fetched_at/checksum | **off** (`UNIVERSE_SYNC_ENABLED`) |
| NSE industry classification, full file (all four levels for every listed company; `UNIVERSE_FILE_URLS` key `classification`, **no default URL**) | WL1 system watchlists: the "Sectors" group on the industry level ("Banking", "Pharma") and for the companies outside the index files | NSE publishes no documented bulk file we could confirm (the per-company quote API carries the levels, but its automated use is restricted by the website terms and is not used); the owner names a file whose terms allow this use, or uploads it; never committed to git | classification ranges (macro sector, sector, industry, basic industry) with source/fetched_at/checksum | **off** (no URL; OPEN_QUESTIONS WL1-1) |
| System watchlists (WL1: "Indices", "<INDEX> stocks (<count>)", "Sectors", "F&O stocks (<count>)") | the watchlist panel (read-only lists, "Copy to my list") | built by the server from the rows above and the broker instrument master; no download of their own, nothing new from outside | `system_watchlists` (one row per list), `system_watchlist_refreshes` (the added / removed symbols and the unmapped symbols per run, last 90 runs) | rebuilt daily after `UNIVERSE_SYNC_HOUR_IST`; Indices works with the sync off |
| NSE archives daily bhavcopies: cash (`cm<DD><MON><YYYY>bhav.csv.zip` until 5 Jul 2024, UDiFF `BhavCopy_NSE_CM_0_0_0_<YYYYMMDD>_F_0000.csv.zip` from 8 Jul 2024), F&O (`fo...bhav.csv.zip` / `BhavCopy_NSE_FO_...`), all-indices close | HB1 daily backfill (10 years) and nightly EOD job: `md_candles` timeframe `day`, sources `nse_bhav:cm|index|fo`; `index_eod`; `md_isin_symbols`; backtests without a broker (flag `backtest_stored_history`, `app/backtest/bar_source.py`: Daily / Weekly only, the run's own result and chart, no bulk export; licence position OPEN_QUESTIONS BSH-1) | free public download files; same terms caveat as the equity lists - the owner confirms the terms before turning the flag on; polite fetching (paced, backoff, no parallel requests); the admin's manual upload of the same files is the alternative | unadjusted daily bars, file SHA-256 per day (`history_backfill_files`), optional raw copy | **off** (`history_backfill` flag) |
| NSE archives all-indices close (`ind_close_all_<ddmmyyyy>.csv`), F&O market lots (`fo_mktlots.csv`), F&O ban list (`fo_secban.csv`) | Screener U1-c `index_eod`, `fo_membership`, `fo_ban_history` | published download files; same terms caveat as the equity lists; manual upload is the alternative | rows with source, fetched_at, checksum | **off** (`UNIVERSE_SYNC_ENABLED`) |
| Each organisation's own broker session - option chains (Phase W recorder) | option backtest pricing from recorded quotes (`option_chain_snapshots`, `app/backtest/chain_recorder.py`) | the session holder's own API agreement (TM94-3 / TM-7): an organisation's chains are read only through its own data session and stored and read only as its own rows; rows from before 23 Oct only for the confirmed owner | strikes around the money every 5 min while its option deployments run, 400 days | on (`CHAIN_SNAPSHOT_INTERVAL_MINUTES`) |

## For the lawyer

Questions about these sources to take to a lawyer before LIVE / public launch (the one lawyer list of readiness item
A6.3, when it is written, takes these over; `docs/COMPLIANCE_IN.md` is generated from the SEBI rule-set).

1. **The system watchlists from the NSE / niftyindices files (owner decision 23 Oct, WL1-3 / WL1-4).** The server
   downloads, once a day after the close (and on the owner's `atp.sh lists-sync`), NSE's published equity / SME / ETF
   lists and symbol changes (`nsearchives.nseindia.com`), the F&O market lots and ban list, and the index provider's
   constituent files (`niftyindices.com/IndexConstituent/...`, 37 indices). From them only NAMES are kept and shown:
   symbols, company names, ISINs, index membership, the sector, the F&O list - no prices from those files on the
   lists (prices come from each user's own broker session) and nothing is committed to the public repository. The
   lists are shown read-only to every signed-in user and can be copied into his own list. Both websites' terms
   restrict automated access and redistribution of their content. Questions: (a) may a SaaS download these published
   files automatically for its users, at this cadence (one request per file per day, a pause between files, an
   identifying user agent); (b) may it display the derived names / membership / sector lists to its signed-in users;
   (c) is a licence from NSE Indices Limited / NSE needed for (a) or (b), and on what terms; (d) what attribution or
   notice the lists must carry.
2. **The all-indices closing file (WL1-9).** With the sync on, the nightly job also stores NSE's daily index closing
   values (`ind_close_all_<date>.csv`) as reference data for the Sector & index view (not shown on the watchlists).
   Question: is storing and showing those closes covered by the same answer as item 1, or does it need a data licence
   (the licensed EOD source of RDY-6)?
3. **Kotak Neo: one ATP server IP for many users, and its data (Part 1B, TM-5 / TM-6; owner decision 23 Oct).** Kotak
   Neo's Trade API takes orders only from a static IP registered with the account, the API session must be created
   from that same IP, and up to 10 family members may share one IP (only the parent account changes it) - from a
   search extract of https://www.kotakneo.com/platform/kotak-neo-trade-api/static-ip-details/ (read 23 Oct 2026; the
   page itself could not be opened). ATP runs on one server with one outgoing IP. The adapter is built with LIVE
   orders and live prices refused in code (`backend/app/brokers/gates.py`; `docs/design/BROKER_KOTAK_NEO.md`).
   Questions: (a) may unrelated users' orders leave from ATP's one server IP at all, and does ATP then have to be an
   empanelled algo provider with Kotak Neo (SEBI retail-algo circular of 4 Feb 2025); (b) if not, is "each user
   registers his own static IP and his orders leave from it" the only allowed model, and what would ATP need to run
   for that (nothing of the kind is built); (c) how the "10 family members per IP" rule reads for a platform IP; (d) may
   ATP show a user his own Kotak Neo prices (quotes, charts, the option chain, the feed) inside ATP - the data-terms
   row is "pending" until Kotak Neo confirms in writing; (e) Kotak Neo says market orders are not allowed for retail
   algo orders: is ATP's marketable limit with the user's protection band (TM-3) the accepted way.

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

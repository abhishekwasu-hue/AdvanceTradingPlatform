From Abhi — Screener spec v2 addendum U1: full NSE universe, sectors and indices in ATP (English). Same working rules as MASTER SPEC and SCREENER_SPEC §1.2 / S2. Draft PRs only, tests with fixtures, no hardcoded dates.

0. Current state (backend/app/instruments): master.py holds only the broker (Upstox) tradable-instrument dump — tradingsymbol, expiry, strike, lot size. No listing/delisting history, no sector/industry, no index constituents, no market-cap buckets, no index master. The screener needs all of these as first-class, as-of data.

1. Data model (new tables, Alembic migrations; keep the broker master as the "tradability" layer and join by ISIN/symbol):
 - securities: isin (PK over time), current symbol, name, series (EQ/BE/BZ/SM/ST…), listing_date, delisting_date, suspension flags, face value, exchange(s) (NSE, BSE), status (active/suspended/delisted), sme flag, etf flag.
 - symbol_history: isin, symbol, valid_from, valid_to (renames and symbol changes).
 - classification: isin, scheme (NSE 4-level: macro-economic sector → sector → industry → basic industry; GICS-style mapping seam), levels, valid_from/valid_to.
 - indices: index_code, name, family (broad / sectoral / thematic / strategy / fixed-income), base date, method, broker symbol mapping (e.g. "NIFTY 50" ↔ Upstox NSE_INDEX|Nifty 50), tradable derivatives flag.
 - index_membership: index_code, isin, weight (where published), valid_from, valid_to — as-of ranges, never overwrite; rebalances append.
 - mcap_bucket: isin, bucket (large/mid/small per AMFI half-yearly list), valid_from/valid_to.
 - fo_membership: isin, lot size, valid_from/valid_to; ban-list history; ASM/GSM history; price-band history.
 - index_eod: index_code, date, OHLC, PE/PB/div-yield where published.
 Every row carries source, fetched_at, checksum.

2. Sources (free NSE/NSE-Indices/AMFI files behind provider seams; polite fetch with proper headers, caching, retry, checksum; manual-upload fallback in the admin UI when a site blocks; no scraping of pages that forbid it — files below are the published downloads):
 - Equity list: NSE archives content/equities/EQUITY_L.csv (symbol, name, series, listing date, ISIN, face value). SME: SME_EQUITY_L.csv. ETFs: eq_etfseclist.csv. Symbol changes: symbolchange.csv. Delisted/suspended lists from the NSE corporate pages' downloads.
 - Index constituents and NSE industry classification: niftyindices.com IndexConstituent CSVs (ind_nifty50list.csv, ind_niftynext50list.csv, ind_nifty100list.csv, ind_nifty200list.csv, ind_nifty500list.csv, midcap150, smallcap250, microcap250, total market, all sectoral: bank, IT, auto, financial services, FMCG, pharma, metal, realty, media, PSU bank, private bank, oil & gas, healthcare, consumer durables, chemicals…; thematic and strategy indices). These files carry the Industry column — use it to populate the 4-level classification; fill the remaining levels from the NSE quote API industryInfo seam or a manual CSV.
 - Index master and daily index closes: NSE archives index bhavcopy (ind_close_all_DDMMYYYY.csv — all indices, one file per day) for index_eod and to discover the full index list; niftiindices factsheets for metadata.
 - F&O: fo_mktlots.csv (lot sizes), fo_secban.csv (ban list), existing nse_expiries job.
 - Market-cap buckets: AMFI large/mid/small-cap classification list (half-yearly).
 - BSE: scrip master seam (later; same tables, exchange column).
 - Broker master (existing sync_upstox) stays the tradability source; reconcile by ISIN nightly and report mismatches (symbol in NSE list but not in broker master, and vice versa).

3. Jobs (idempotent, scheduled after market close; schema-versioned; data-quality gates per SCREENER_SPEC §5): equity list daily; constituents daily (diff → append membership ranges; alert on rebalance); index bhavcopy daily; lots/ban daily; AMFI buckets on publication; symbol-change daily; full reconciliation nightly. Backfill: load available historical constituent files and index bhavcopies to build as-of history as far back as the files allow (document gaps; never fabricate history).

4. Universe picker (screener + strategy builder + watchlists): any index (broad/sectoral/thematic/strategy), sector/industry/basic industry, market-cap bucket, F&O members, series, SME/ETF toggle, custom lists, set algebra (NIFTY 500 ∩ F&O − banks), saved universes, as-of universe for historical runs (SCREENER_SPEC §2.7). Classifiers for ScreenQL: Sector(), Industry(), IndexMember("NIFTY 200"), McapBucket(), IsFnO().

5. UI: Markets → "NSE Universe" page: counts by series/sector/index, data freshness, last rebalance diff, reconciliation report, manual upload; symbol page shows all memberships and classification history; index page shows constituents with weights and sector heatmap (feeds the breadth/RRG panels).

6. Tests: fixture CSVs for every source; as-of membership queries (a stock that left an index must not appear in historical screens after its exit date and must appear before it); symbol rename continuity by ISIN; delisted stock retained in historical universes; reconciliation mismatch report; job idempotency (re-run produces no duplicate ranges).

7. Order: U1-a tables + migrations + equity list + symbol history → U1-b index master + constituents + classification + membership as-of → U1-c index bhavcopy + index_eod + lots/ban/AMFI → U1-d universe picker + ScreenQL classifiers + UI page → U1-e backfill + reconciliation report. Slot this inside SCREENER_SPEC S2 (it is the concrete version of §1.2).

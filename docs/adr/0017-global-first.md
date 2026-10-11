# ADR-0017: Global-first data and execution model; India is the first configuration, not the shape of the code

**Date:** 2026-10-10 · **Status:** provisional · **Parts:** B, E, D (MASTER SPEC v1.2, v1.3)

## Context
The code grew India-first: symbols are NSE strings, sessions are IST and NSE's calendar (with per-exchange families
since Phase O2), costs are `india_costs.py`, the currency is INR with FX groundwork (Phase P), compliance is SEBI-shaped.
v1.2 asks for global markets (venues by MIC, ISIN/FIGI, multi-currency, settlement, corporate actions, a cost model per
venue, a jurisdiction rules engine, tax seams) and v1.3 makes crypto (24x7, fractional quantities, quote currencies,
maker/taker fees, funding) the first non-Indian phase - **without delaying the Indian PAPER go-live**.

## Decision (provisional)
1. **Instrument identity**: a platform `instrument_id`; venue listing = (MIC, venue symbol); optional ISIN / FIGI;
   specs (tick size, lot / contract multiplier, quantity step - fractional allowed, quote currency, settlement
   currency, expiry, strike) are **versioned as-of** (`instrument_master_versions`, ADR-0013). NSE/BSE/NFO/MCX map to
   their MICs (XNSE, XBOM, ...); crypto venues get a platform venue code where no MIC exists.
2. **Time**: everything stored in UTC; each venue has a timezone and a session calendar (holidays, half days, 24x7) in
   data; "trading day" is a per-venue function, not IST.
3. **Money**: every amount carries its currency; P&L in the instrument's quote currency plus a reporting currency via
   the FX table (Phase P); fees in the currency the venue charges.
4. **Costs**: `CostModel` registry per venue (`india_costs.py` becomes the XNSE/XBOM/... model; crypto maker/taker,
   funding; IBKR tiered) - the backtest's BrokerageModel (C2) is this registry.
5. **Rules**: the jurisdiction rules engine of part D (`app/compliance/rules.py`, rule-sets as data): IN-SEBI first;
   crypto (India resident: spot only, FIU-IND-registered exchanges, 30 % tax + 1 % TDS seam) and others as files.
6. **Tax**: a `TaxModel` seam per jurisdiction (India FY report exists - Phase P) - reports only, never advice.
7. **Order of delivery**: Indian PAPER go-live on Hostinger first; then crypto paper (CoinDCX spot); then Binance /
   Kraken; then IBKR (v1.3). Each new venue is a provider on the ADR-0016 seams plus data rows, not a code branch.

## Consequences
- Part B's schema is written venue-neutral now (cheap now, expensive later); existing NSE strings keep working through
  the listing map during migration.
- Session/calendar code (Phase O2 families) generalises to per-venue calendars from data.
- Compliance and costs become data files reviewed like code, with tests that every rule/cost row is exercised.
- Nothing in this ADR changes Indian behaviour on its own; each step lands behind its part's PRs and flags.

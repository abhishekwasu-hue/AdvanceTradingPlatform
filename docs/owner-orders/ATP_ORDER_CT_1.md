# OWNER ORDER - CT-1: Charts + Terminal consolidation (30 Oct 2026), with the owner's addendum

Verbatim, as received (roadmap CT-1: P1 for T1-T3 and C3, P2 the rest). Audit note: docs/design/CT_AUDIT.md (written first).

```
OWNER ORDER — CHARTS + TERMINAL CONSOLIDATION (30 Oct). Roadmap row "CT-1" (P1 for T1–T3, P2 rest). File INBOX verbatim / DECISIONS /
roadmap / README. Owner: "a lot of repetition". Overseer audit (main 124eafc, file:line; benchmarks Kite Terminal Mode, TradingView
chart trading, Dhan DEXT T3, Upstox multi-chart — sources in docs/design/CT_AUDIT.md, write it first). Rule: ONE component per kind,
linked by symbol; layouts place widgets, they do not re-implement them. Re-verify each item (first report = confirmed / not reproduced);
one PR per group; self-review; tests; before / after phone 390 + tablet 820 + desktop, dark + light, ONE comparison page per PR; ≤ 15-line
reports; UI-28 rules apply. Nothing deleted until the owner's ✅ on the comparison page (old paths hidden behind Advanced meanwhile).

P1 — trader-blocking
T1 Terminal chain has no expiry picker (api.optionChainLive(underlying) with no expiry, TerminalPageV2.tsx:148) → add the same expiry
   selector as OptionChainPageV2.tsx:208, default "next weekly on expiry day" (AP5 rule).
T2 Terminal underlyings fixed to NIFTY / BANKNIFTY / FINNIFTY / SENSEX (layoutModel.ts:73) → the same symbol list and SymbolPicker as
   the chain page; on phone the chart's symbol can be changed (EmbeddedChartV2 gets onSymbol when narrow, TerminalPageV2.tsx:184).
T3 Tablet bug 768–1023 px: layout controls show (md:flex, :247) while the page renders phone tabs (NARROW ≤ 1023, :57) → controls only
   when the grid renders (≥ 1024), one breakpoint constant.
T4 Exit all / group exit only in Terminal (PanelsV2.tsx:344) → move the actions into PositionList so My trades, Terminal, Money behave
   the same (one code path, exits never blocked).

P2 — one component per kind
C1 Chart: ChartV2 + ONE ChartToolbar with full / compact modes (compact keeps SymbolPicker + timeframe + indicators); EmbeddedChartV2's
   own toolbar and the free-text symbol input (PanelsV2.tsx:76-81) go; retire the ProChart windows (pages/ChartWindow.tsx,
   ContractChartWindow.tsx, ChartWorkspace.tsx — App.tsx:330-345) after their entry points open ChartV2; CandleSnapshotV2 stays only
   for static journal images.
C2 Multi-chart: one implementation (MultiChartV2, /api/charts/layouts) owned by Charts; drop the Terminal "Multi-chart" preset
   (layoutModel.ts:94-98) or make it render MultiChartV2 — one layout-save system.
C3 Option chain: ChainTableV2 with a `compact` prop used by OptionChainPageV2, the Terminal chain panel (PanelsV2.tsx:119-233) and the
   cockpit slice (CockpitDeskV2.tsx:143) — one table, one strike window rule, columns menu everywhere.
C4 Chain summary: keep SummaryStripV2 (top); ChainBottomStripV2 (OptionChainPageV2.tsx:278; ChainTradingV2.tsx:196-200) shows only
   the basket badge + ATM button, not the same 4 figures again.
C5 Basket: one basket view (BasketPanelV2 body) embedded in Terminal and cockpit; SpreadBuilderV2 stops computing credit / max loss
   itself; delete the test-only BasketPanelBodyV2 (PanelsV2.tsx:480). One calculation of legs / credit / max loss / breakevens.
C6 Symbol picking: SymbolPicker everywhere (Charts, MultiChart, Watchlist, chain page, Terminal); one symbol universe.
C7 Linked by symbol: a page-level "active symbol + expiry" that chart, chain, order panel and watchlist follow (colour link groups as in
   MultiChartV2); a WatchlistPanel as a Terminal panel.
C8 One PAPER badge, one word ("PAPER"), once per screen — replace PracticeFrame / mode chip / "· Practice" / "practice account" text
   (terminal/statusModel.ts:40, ticketModel.ts:46, PanelsV2.tsx:128,457).
C9 Freshness: shell FeedDot + the chart's LiveChip only; remove the Terminal feed chip (StatusBarV2.tsx:101) and duplicate "as of"
   chips; chart settings popover once per page.
C10 One "Ask AI / Read chart" entry in the shell (today chain-page Ask AI + Charts Read + Analyze, separate code).

P2 — layout
L1 Charts (analysis): phone = ONE tab row (hub tabs Chart · Option chain · Futures · Overview; Multi-chart moves into the toolbar);
   toolbar on phone = Symbol · Timeframe · Indicators · Draw · More (rest in More); desktop unchanged.
L2 Terminal (trading): phone tabs Chart · Chain · Order · Positions · Orders with the status bar; desktop presets "Option seller" and
   "Quick trade" only; positions + orders as one bottom panel with tabs (TradingView Account Manager pattern).
L3 Text: spread-builder (≈200 chars, SpreadBuilderV2.tsx:105-108), empty basket (≈190, :56-58), exits note (≈130, :109-110), empty
   order (115, PanelsV2.tsx:246), practice note (100, :128), page intros (Charts :231, Terminal :240) → one line + Details or "?" icon.
PROOF: the routes table before / after ("a chart" 8 → ≤ 3 component types; "an option chain" 3 tables → 1); NIFTY on expiry day in
Terminal choosing the next weekly; Exit all from My trades; tablet 820 px Terminal.
OWNER ADDENDUM — CT-1 (30 Oct): "The option chain on the Terminal is not good." Raise C3 to P1 (beside T1–T2) and do it fully:
- Remove the Terminal's own chain table (PanelsV2.tsx:119-233 ChainPanelV2) — the Terminal chain panel renders the SAME ChainTableV2
  as /option-chain (no compact copy with fewer features). Everything the chain page has, the Terminal has: expiry selector (default
  next weekly on expiry day), any index / stock via SymbolPicker, Calls | strike | Puts with ATM centred, ITM shading, OI + OI-change
  bars, LTP / change Signed, IV, optional Greeks, columns menu, strike range ("ATM ±N" chips), live ticks with tick flash.
- Tap a price → the same ChainActionSheetV2 (Buy / Sell / Add to basket / Alert / Chart) → the one order ticket / one basket (C5);
  the basket stays visible beside the chain on desktop, as a bottom sheet on phone.
- The Terminal chart, chain and order panel follow one active symbol + expiry (C7): change it once, all three change.
- SummaryStripV2 (spot Signed Δ %, ATM, PCR, max pain, DTE) on top of the Terminal chain too; no second strip.
- Phone: the Chain tab is the full table with a sticky strike column and horizontal scroll inside the table only (no page overflow).
PROOF: Terminal chain vs Option chain page side by side on phone + desktop showing the same rows / columns / expiry; NIFTY on expiry
day switched to the next weekly; tap → ticket → basket.
```

## Filing notes

- TERM-2 (ATP_ORDER_TERM2.md) supersedes CT-1 T1-T4, C3, C5, C7 and L2 for the Terminal; CT-1 keeps the Charts-page items
  (C1, C2, C4, C6, C8-C10, L1, L3 and the chain page / cockpit side of C3 and C5). The addendum's Terminal chain demands are built
  in TERM-2 step 2.

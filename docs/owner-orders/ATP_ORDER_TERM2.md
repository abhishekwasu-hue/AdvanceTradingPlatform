# OWNER ORDER - TERM-2: Terminal rebuild (30 Oct 2026)

Verbatim, as received (roadmap TERM-2, P1). Design note with the benchmarks: docs/design/TERM2.md.

```
OWNER ORDER — TERMINAL REBUILD (30 Oct). Owner: "Terminal is not up to the mark." Supersedes CT-1 T1–T4, C3, C5, C7 and L2 for the
Terminal (CT-1 keeps the Charts-page items). Roadmap row "TERM-2" (P1). File INBOX verbatim / DECISIONS / roadmap / README.
Benchmarks (cite in docs/design/TERM2.md first): Kite Terminal Mode (widgets + presets), Dhan DEXT T3 (linked widgets, layouts),
TradingView (trade from chart, one bottom Account panel), Sensibull (chain → builder → payoff → basket). Principle: the Terminal OWNS NO
COPIES — it places the same components the app already has, linked by one active symbol + expiry. Re-verify the audit items first;
one PR per step; self-review every diff; tests; behind the `terminal` flag (old Terminal reachable under Advanced until owner ✅);
before / after phone 390 + tablet 820 + desktop, dark + light, ONE comparison page per step; ≤ 15-line reports; UI-28 rules; ADR-0004 /
ADR-0006 / DEF-0 unchanged.

STEP 1 — Context bar (all sizes): SymbolPicker (any index / stock), expiry selector (default: next weekly on expiry day), spot + Signed
  Δ %, VIX LevelChip, PCR, DTE, one PAPER badge. One active symbol + expiry drives every panel (store at page level; colour link groups
  as in MultiChartV2).
STEP 2 — Panels = existing components, no copies: Chart = ChartV2 + ChartToolbar (compact) with the user's legs, breakevens and stops
  drawn on the chart; Chain = ChainTableV2 (full, same as /option-chain: ATM centred, ITM shade, OI / OI-change bars, IV, optional
  Greeks, columns menu, ATM ±N chips) + SummaryStripV2; Basket = BasketPanelV2 body (legs, payoff graph, max profit / loss, breakevens,
  POP, margin required / available recalculated on each lot change, the user's own SL / target / trailing — blank = off, one Place
  button) — ONE calculation of credit / max loss / breakevens; Positions + Orders + Alerts = ONE bottom panel with tabs (TradingView
  Account Manager pattern), PositionList grouped by strategy with combined P&L (Signed), Greeks, Exit / Exit part / Reverse / Roll,
  Exit all (same component as My trades). Remove: ChainPanelV2 (PanelsV2.tsx:119-233), SpreadBuilderV2's own figures, BasketPanelBodyV2,
  EmbeddedChartV2's own toolbar + free-text symbol, the Terminal feed chip, the Terminal Multi-chart preset.
STEP 3 — Layouts: desktop presets "Option seller" (chart | chain | basket, bottom panel) and "Quick trade" (chain | ticket, bottom
  panel); panels resizable, saved per user (one save system). Phone: context bar + tabs Chart · Chain · Basket · Positions · Orders;
  chain tap → bottom sheet (Buy / Sell / Add to basket / Alert / Chart); sticky strike column, no page overflow. Tablet 768–1023 =
  phone layout without desktop controls (one breakpoint constant).
STEP 4 — Beyond the best (each behind a flag, PAPER first):
  R1 One-tap Roll: move a position / strategy to the next weekly (exit + new legs as one basket, preview of credit and max loss, the
     user confirms; exits never blocked).
  R2 What-if on open positions: adding a leg to the basket shows the combined payoff of open positions + the new legs.
  R3 Distance from each short strike to the nearest area (from the reader / AUD T1), shown on the position row and on the chain.
  R4 Margin required / available pair on every lot change (broker margin API when the adapter has it; else an honest "estimate").
STEP 5 — Text: no paragraph on screen; one-line empty states with one action; explanations behind "?"; numbers Signed and coloured.
PROOF: a full flow on phone and desktop — open Terminal → choose NIFTY, next weekly on expiry day → tap two strikes into the basket →
payoff + margin → Place (PAPER) → position appears grouped with combined P&L → Roll to the next weekly preview; plus the "routes /
components before → after" table (chain tables 3 → 1, chart toolbars 2 → 1, basket calculations 2 → 1).

Do step by step
```

## Filing notes

- CT-1: no order, roadmap row or design note named CT-1 exists in this repo (every branch grepped 30 Oct). The order is read
  as written: TERM-2 replaces every Terminal item of that list; the Charts page is not touched by TERM-2.
- `terminal` flag: TERM-2 builds the new Terminal behind it; the current Terminal stays reachable under Advanced until the
  owner's ✅.

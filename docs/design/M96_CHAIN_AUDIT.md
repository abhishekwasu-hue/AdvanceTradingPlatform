# M96 Terminal › Option chain - audit (A1, 11 Nov) and build plan

Read-only audit of origin/main 11081161, by four parallel agents (UI, backend data, order ticket, phone layout). No code was changed.
Before screenshots: 390 + 1440, light + dark (sent to the owner as a ZIP).

## A1 audit table (top to bottom as the Terminal shows it today)

| # | Part | Component / file | Shows | Data source | Refresh | Units | Phone / layout issues | Bugs / gaps |
|---|---|---|---|---|---|---|---|---|
| 1 | Context bar (Terminal) | TerminalPageV2 `[data-block=terminal-context]` | Symbol, expiry, spot, VIX, PCR, ATM, expiry in | user's broker | with chain | - | repeats the chain header and cards on phone; ATM row starts below the first screen (y 1043 of 844) | duplicate facts |
| 2 | Chain header | OptionChainPageV2.tsx:192-209 | "Option chain" h1, symbol, expiry, Columns menu | instrument master / NSE expiry file | on change | - | 1440 panel: pickers row 567 px in a 443 px panel, expiry box clipped; phone expiry "3 Nov 2…" cut; h1 repeats the panel title | - |
| 3 | Chain notes | model.ts:74-84 | market closed / OI change since / building | /live | 15 s open, 5 min closed | - | - | - |
| 4 | Summary strip (8 cards) | SummaryStripV2.tsx:269 | spot (+chg), futures, ATM, PCR (OI) gauge, max pain (+pts), ATM IV, DTE, OI walls | user's broker (/live), spot from live ticks / 5 s LTP | 15 s / 5 min | strikes only | 1440: values cut ("24,420.!"), labels cut ("ATM …"), captions cut, OI walls wraps 4 lines; cards fill the panel so the table is not visible on open; phone captions cut | spot card flash colour can disagree with the change arrow when two spot sources differ |
| 5 | View toggle Chain / Charts / OI | OptionChainPageV2.tsx:69-86 | 3 views | - | - | - | - | - |
| 6 | Strikes chips | ChainLadderV2.tsx:294-303 | All / ±5 / ±10 / ±15 | client | - | - | 1440: covered by the sticky bottom bar | - |
| 7 | Chain table | ChainTableV2.tsx:58 | per side: OI + bar, OI chg, volume, IV, LTP, (Greeks, bid/ask via Columns), strike, ATM tint, ITM shading | user's broker /live; Greeks computed (Black-Scholes) from IV | 15 s / 5 min (poll, no websocket) | OI via formatCount: lakhs only from 1 lakh up, below that plain ("50,000" next to "1.2 L"); OI change and volume never in lakhs | phone: put OI cut at the right with no scroll hint (31 px); row/tap height 37 px (< 44); 1440: sticky strike column covers call OI change when scrolled; "ATM" label contrast 4.16 (< 4.5); Terminal default panel width hides IV and OI change | no lakhs / contracts toggle; units not labelled (broker sends raw OI, never converted) |
| 8 | P markers | ChainTableV2.tsx:212 | held PAPER legs | /paper/fno/held | 30 s | - | - | flag paper_account |
| 9 | Area marks | ChainTableV2.tsx:201 | distance to nearest area | /today/my-day | 60 s | - | - | needs term_strike_area |
| 10 | Action sheet (tap CE / PE) | fno/ChainActionSheetV2.tsx | Buy / Sell, basket, chart, watchlist, alert, Exit | - | - | - | - | basket chips say "1 lot" while lots stay empty unless saved (M68) |
| 11 | Order ticket | fno/OrderTicketV2.tsx, PanelsV2 OrderPanelV2 | contract, lots, order type, product, price, trigger, SL | /paper/fno/order(+preview) | on demand | lots | - | prefill = contract only, lots / type / product only from his saved settings (M68, tested); NO target field; PAPER only - no LIVE route exists for the ticket |
| 12 | Bottom bar | ChainTradingV2.tsx:197-225 | ATM recentre, OI change count / %, basket badge | /live | with chain | - | 1440: covers the strikes chips and the top rows | - |
| 13 | Charts view | ChartsViewV2.tsx | 10 charts (OI by strike, OI change, multi-strike OI (3), cumulative OI, PCR / max pain / straddle / ATM IV over the session, IV smile, futures) | /option-chain/charts (live + stored slots) | with chain | L / Cr | - | OI change baseline = collector's first OI of day (differs from the table's baseline) |
| 14 | OI view | OiViewV2 / OiTabV2 | banner, OI history 5/10/15 min, OI by strike, settings + alerts | stored collector slots (oi_snapshots, 5 min, one expiry) | 60 s while open | L / Cr | - | Zerodha / Kotak / Shoonya chains carry no spot -> slots stored EMPTY; on expiry day the collector stores the expiring contract while the view may show the next -> history dropped |
| 15 | Analysis tabs | AnalysisTabsV2 | S/R, OI build-up (ATM ±5), PCR, max pain, expected move (spot ± straddle), IV smile | /option-chain/analyses (live + stored) | with chain | L / Cr | phone: tabs and tables cut at the right (scroll inside) | PCR / max pain windows differ from the cards (whole chain vs ATM ±5/±6/±10) |
| 16 | My analyses + Ask AI | MyAnalysesV2, AskAiV2 | saved specs, AI question | /option-chain/ask, /eval | on demand | - | - | - |
| 17 | No-broker view | EodChainV2 | last NSE F&O close + OI | md_candles nse_bhav:fo | once | L / Cr | - | Terminal still polls /live without a broker |

Data facts (backend): OI is the broker's raw number, never converted, never labelled as contracts or shares. Change in OI has three baselines (table: broker previous-session field, else first reading of the day kept in Redis for 1 h; charts: collector's day baseline; build-up: first stored snapshot). PCR has five windows. IV uses the user's rate on the live page and 7 % in analyses / charts. DEX / GEX, IV percentile / rank, volume PCR, change PCR, per-strike PCR, volume / OI and per-strike OI alerts do not exist. The 1-minute chain store (md_tenant_chain_snapshots: all fields, 2 expiries, NIFTY / BANKNIFTY / SENSEX, flag chain_history) can serve from-to ranges, replay and multi-expiry; /api/chain-history/range exists (≤ 31 days).

Correctness against the broker's own chain (OI, OI change, LTP, IV, PCR for one expiry) cannot be measured from the sandbox (no broker session): it needs one side-by-side screenshot from the owner (broker app chain and ATP Terminal chain, same expiry, same minute).

## N1 status
Exists: multi-strike OI (3 strikes), PCR (OI) and PCR over time, max pain and over time, ATM IV, IV smile, ATM straddle chart, Greeks columns, OI alerts (8 types, banner level).
Partly: buildup tags (analysis tab, ATM ±5 only, not in the table), walls shift (text only), IV change (ATM vs 5-session average), expected move (tab only, not on the price chart).
Missing: OI change over a from-to range / slider / replay, multi-expiry view, change PCR, volume PCR, per-strike PCR, IV percentile / rank, DEX / GEX + flip, volume / OI + unusual activity, OI lakhs / contracts toggle, per-strike OI threshold alerts.

## Build plan (after the owner's word on the questions)
PR-1 L1 + layout: banner (one source of truth for spot / future, ATM, expiry, PCR, max pain, data time) and the OI-difference table on top, the existing table below; fix layout issues 1-13 of the phone audit; OI in lakhs everywhere + lakhs / contracts toggle.
PR-2 O1: tap CE / PE in the OI-difference table -> the ticket in place, contract only.
PR-3..n N1 sections in groups: (a) buildup tags + walls shift + PCR variants; (b) from-to range / replay + multi-expiry from the 1-minute store; (c) IV rank + DEX / GEX + volume / OI; (d) expected move on the price chart + per-strike OI alerts.
Each with T1 tests and before / after proof.

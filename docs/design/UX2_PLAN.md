# UX-2 plan — Session B (UI redesign), for the owner's OK (26 Oct)

Order: ATP_ORDER_UX2.md (UX-1..UX-10, "UX-3 replaced", TA-1..TA-4) + the UX1_AUDIT worst 10 + TopBarV2 H9 (DECISIONS 26 Oct, #552).
Roadmap: all PRs below sit under the one item **UX-2** (already "in progress"), so R2 (max 5, #552) does not grow.
All new UI behind `ui_v2`; tokens only (check-v2-colours = 0); no order path / backend trading logic touched;
no edits to CX/SX (#553), the Watchlist page (#554) or TimeframePicker (#540). Migration: **no** for every PR below.

## PRs in order (one `claude/ux2-<name>` branch + draft PR each)
| # | Item | Screens changed | Size | Notes from the code |
|---|---|---|---|---|
| 1 | H9 title wrap (CA7) | TopBarV2 on every page (phone 360/390) | S | `[overflow-wrap:anywhere]` cuts "Watchlis/t"; wrap only between words, a long single word shrinks one step instead |
| 2 | UX-6 no "About this page" | 13 pages using `PageIntro` | S | PageIntro text moves into the header (?) (HelpPopover); one component change + guard test "no paragraph above first data" |
| 3 | UX-2 symbol-first search | header search (CommandBarV2), all pages | M | frontend only: `/api/instrument-master/search` for stocks/indices, `/api/charts/contracts/search` for "nifty 22700 ce" (flag `chart_contracts`); recent symbols first (SymbolPicker store), pages after; Ctrl/⌘ K; Enter opens the chart |
| 4 | UX-5 shared combobox | Charts, Option chain, Terminal, Performance, News | M | new `ui/Combobox` built from SymbolPicker's keyboard pattern (filter, recent, groups, phone bottom sheet); short fixed lists = segmented control; the 5 pages only (53 files have `<select>`; the rest later in page waves) |
| 5 | UX-7 live feel | header + Terminal, Charts, Option chain, Today | M | tick flash (reduced-motion safe), feed dot from `live/sse`, stale badge, skeletons for every panel; order states are display only |
| 6 | UX-4 font + numbers | all pages | S-M | self-host Inter (drop fonts.googleapis.com), `tabular-nums` on every price/qty/P&L, one type scale (live price largest), 2 decimals, Indian grouping |
| 7 | UX-8 density | Settings → Appearance + tables/rows | S | Comfortable / Compact as tokens (padding/row height), saved per user on this browser |
| 8 | UX-3 + TA-1..TA-4 Terminal | Terminal (desktop + phone) | M-L | watchlist panel docked left in every preset, linked to chart/chain, "+ add symbol" uses PR 3; chain CALLS \| PUTS header, in-panel expiry, ATM centred, OI bar; empty panels collapse; help behind (?); Option-seller default layout |
| 9 | UX-10 wave 1: Today (TD-1) | Today (+ phone watchlist strip) | M | tiles, one number / one line per card, logical colour |
| 10 | UX-10 waves: Charts, Option chain (OC-1), My trades | those 3 pages | M each (3 PRs) | H1 order header → badge → cards → visual → details |
| 11 | UX-9 ticket LOOK | order ticket (Terminal, Charts) | M | defaults visible, advanced collapsed, review block (price, charges est., margin, max loss) from existing data; payload / API / validation / exits unchanged |

Each PR: vitest next to the component + guards (userFriendlyPass, colour, nav), Playwright e2e of the changed pages,
regenerate only baselines that fail because of the redesign (each image looked at). One comparison page per milestone
(390 + 1440, light + dark): M1 = PRs 1-2, M2 = 3-4, M3 = 5-7, M4 = 8, M5 = 9-11. Owner ✅ before each merge.

## Owner decisions — approved 26 Oct with these defaults
1. Terminal overlap with Session A (T2/T3 also edit `terminal/`): **default** PR 8 starts only after A's T2/T3 PR merges; B does PRs 1-7 first.
2. UX-9 phone LIVE "press-and-hold" changes a LIVE confirm rule: **default** not built here; ticket LOOK only, current confirm stays.
3. Font: **default** Inter (already the product font, now self-hosted); option IBM Plex Sans.

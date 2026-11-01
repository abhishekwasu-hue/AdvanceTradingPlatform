# M75 review: the Copilot trading workspace against the code (CLAUDE.md §6, before any build)

Order: docs/owner-orders/ATP_ORDER_M75.md (CW-1..CW-8; recorded on `claude/inbox-m71-pl7`). Builds on M72
(docs/design/M72_REVIEW.md, findings F1..F22, rows M72Q-1..5). Base: `origin/main` f6b795b0 (1 Nov). Read by grep +
line ranges. Builds nothing. The no-navigation inventory (CW rule 1) is written in parallel by another agent and is NOT
part of this file; where a finding below touches a navigation it only points to it.

## 0. Summary

- No chart workspace in the Copilot today; the answer mini-chart draws an area as two dashed price lines (the overlaps).
- CW-2 facts mostly exist in ATP's readers; the M66 contract lacks trend per TF, BOS / CHoCH, tests, divergence.
- Plan / order / exit parts exist, PAPER-only routes, no LIVE manual path (TT-1). No trigger plans; no adapter has GTT
  (TM-23: own engine by default, GTT a per-user opt-in).

## 1. Per item: exists / missing / risks

### CW-1 Chart workspace inside the Copilot

Exists
- Copilot v2 page `frontend/src/copilot/chat/CopilotPageV2.tsx:23-37`: tabs Ask · Read the chart · Analyze a chart ·
  Coach (`CopilotTabsV2.tsx:7-9`, "at most four tabs", M60). "Read the chart" = Market Pulse only
  (`CopilotFoldedV2.tsx:41-42`): no chart.
- The chart in an answer: `AnswerV2.tsx:121-126` (`EmbeddedChartV2`, `className="h-48"`, `openLink={false}`) and the
  "Open chart" link to the Chart tab (`AnswerV2.tsx:153-156`) - a navigation (inventory).
- Reusable chart parts: `chart/EmbeddedChartV2.tsx:55-105` (compact `ChartToolbar` with SymbolPicker via `onSymbol`,
  timeframe picker TF-1, live chip), `chart/ChartV2.tsx:68-160` (`ChartCore`, `onEngine` -> `LightweightEngine`
  layers), "Read this chart" hook `chart/ReadChartV2.tsx:16-35` (reads on symbol / TF change - the "auto" pattern).
- Option-contract series: `backend/app/charts/contracts.py:1-10,53-57` (NFO keys of the F&O bhavcopy only).

Missing
- A workspace view inside the Copilot (chart full-width on phone + side panel / bottom sheet for analysis and plan).
- F&O symbols in the picker: `ui/SymbolPicker.tsx:87-92` searches INDEX + EQ only ("futures and options are not
  charts of their own here"). Intraday bars for an option / future contract: the lake has NFO daily keys only; BFO /
  MCX contracts have none (`contracts.py:5-7`).
- Auto-start of the analysis on open (today "Read this chart" is a toolbar switch; the AI part needs an upload).

Risks
- Five tabs break the M60 "at most four" rule: provisional - the workspace opens in "Read the chart" (its name), Market
  Pulse stays in the same tab under it (nothing removed, §8).
- Option contract charts on intraday timeframes must say "data not ready" rather than draw from a wrong source.

### CW-2 Annotations drawn by code + the Vision card

Exists
- ATP read service `backend/app/charts/read_chart.py:1-21,228`: structure (direction, last event, protected level),
  areas from the stored zones (each with its own timeframe, never merged), pullback, commitment candle; flags `ui_v2` +
  `read_chart` (`platform/flag_catalog.py:182`). Drawn as layers: `chart/readChart.ts:60-74` (bands + protected line
  + commitment marker). Week / month not offered (`readChart.ts:52-53`).
- Price-action layer BOS / CHoCH, liquidity, trendlines (`charts/price_action_layer.py:255`); zone lifecycle
  `price_action/zones/lifecycle.py:40-60` (fresh / tested / flipped / invalidated); zone touches in
  `charts/zones_layer.py:14-15`; RSI divergence as a screen function (`screener/amw_functions.py`, flag
  `screenql_amw_functions`).
- Band painter `charting/geometry.ts:209-228` (tinted rect + label at the right edge) and `charting/primitives.ts:93`
  (`LayerPrimitive`); `LightweightEngine.setLayer` (`charting/lightweight.ts:238-253`).
- M66 contract `backend/app/engine_provider/contract.py:150-289`: `Area` (side, state token, band, timeframe,
  formed_at), `Swing` (HH / HL / LH / LL), `HigherTimeframeSide`, `Signal` (internal `invalidation`), interface 1
  (`:31-33`). The engine is read only by the worker (`workers/engine_runner.py:603`); flag `trade_engine` owner only.
- Vision: the method's instructions are in the private package (`chart_analyze/provider.py:1-17`, `HybridProvider.
  hybrid_instructions(width, height, facts)` + `parse_hybrid` `:59-66`); the hybrid mode gives it ATP's generic facts and
  ATP's own rendered chart, never a price read from the image (`chart_analyze/hybrid.py:1-19`, `reading_png.py:1-11`).

Missing / wrong today (the owner's overlaps, root causes)
- "Bands drawn as dashed lines only" + "Area top hiding the axis": the answer chart draws the area as two price lines
  (`copilot/chat/model.ts:275-279` "Area top" / "Area bottom") which `ChartCore.setLevels` creates as
  `LineStyle.Dashed` with `axisLabelVisible: true` and the title (`chart/chartCore.ts:428-437`): the titles sit on the
  price axis and cover its numbers; no band is drawn.
- "Auto" / "Reset" over area labels: `ChartViewControls` is absolutely placed at the bottom right of the pane, z-10
  (`chart/ChartViewControls.tsx:36-41`); band labels are painted at `x = width - 4`, right-aligned, just above the band
  (`geometry.ts:225`); price-line titles also sit at the right. On a 192 px chart they collide. There is no collision
  rule in the painter (labels of near bands also overlap each other).
- Contract gaps for CW-2: trend per timeframe (only one `higher_side`), BOS / CHoCH events, an area's test count, RSI
  divergence, "next areas", the invalidation level as a shown value. A change is a new interface number that keeps 1
  working until the private package follows (`contract.py:31-33`).
- No request-time engine read (a user opening a chart) - only the worker cycle.
- The Vision card on a chart the user did not upload: hybrid runs only on an uploaded item
  (`chart_analyze/routes.py:158-246`).

Risks
- State words: the order lists ALIVE / FALSE BREAK / BEYOND_NOT_BROKEN / BROKEN / DEAD (Trade's lifecycle); ATP's zones
  have four other states and the analysis contract a third set (`chart_analyze/contract.py:57`); `read_chart.py:16-18`
  says Trade's lifecycle words are not shown until the reader port (P5-1). Mapping one onto the other would invent
  method logic (M75Q-4).
- Invalidation levels: `contract.py:18,228-241` and CLAUDE.md §3 keep `Signal.invalidation` internal (M75Q-3).
- Private boundary: engine tokens, model ids and settings never reach a public file, prompt or screenshot (M72 F9).

### CW-3 Download PNG + one-page PDF

Exists
- Server PNG, deterministic, standard library: `ai/chart_png.py:1-20`, hybrid reading picture `chart_analyze/
  reading_png.py:1-11` (bands, protected line, trigger box; route `routes.py:248`).
- Browser PNG: `chartAnalyze/exportImage.ts:1-92` (overlay + disclaimer, Web Share), `fno/snapshotImage.ts:1-69`.
- PDF: only the journal's text-only writer (`journal/export.py:165`, route `journal/stats_routes.py:68-75`), no image.
  Pillow is already a dependency (`backend/requirements.txt:26`).

Missing
- A PNG of the workspace chart as drawn (canvas of `ChartV2` + the layer; lightweight-charts `takeScreenshot` is not used
  anywhere) and a one-page PDF (chart + table + explanation + IST timestamp + "information only").

Risks
- The PDF / PNG of his own chart may carry the symbol and the area as a price range (CLAUDE.md §3 "his OWN signals");
  a share link would be a public surface (no names, no prices, older than three months) - keep CW-3 download-only.

### CW-4 Trade plan panel

Exists
- Options Builder engine: `options_builder/payoff.py:13-66` (payoff + combined Greeks), `greeks.py`, `model.py`
  (IV / days shift, POP), `metrics.py:64-70` (margin ESTIMATE; SPAN not in PAPER), `selectors.py`; UI parts
  `frontend/src/optionsBuilder/PayoffCanvas.tsx`, `MetricsCard.tsx`, `LegTable.tsx`.
- Broker margin: `get_basket_margin` only in `brokers/upstox.py:583` and `brokers/zerodha.py:390`;
  `paper/fno_basket.py:8-11` calls none (FNO-2-1).
- Exits at entry: single ticket `stop_loss` (`paper/fno_routes.py:103-122`); basket SX (SL / target / trailing over the
  structure, `paper/fno_routes.py:465-477`, `execution/strategy_exits.py:1-20`); CX group exits (`fno_routes.py:323-410`);
  resting stop / target on a position (`fno_routes.py:209`); trailing / break-even / time exit on positions
  (`trading/exit_rules.py:80-126`, used by `position_monitor.py:1322`).
- M68 registry fields: `me/trade_settings.py:166-200` (stop / target / trailing kinds and values, time exit, combined
  measure), Options `:203-242`.

Missing
- One panel that takes any shape (option legs, future, equity) beside the chart; equity uses the other ticket
  (`paper/ticket_routes.py:90-98`) - two bodies, two previews.
- At entry: target, trailing and time exit on the single ticket (only `stop_loss` is in `OrderBody`); time exit on SX.
- AI suggestions from the user's own rules shown as suggestions (M72 PR-4 / PR-9, M72Q-4 / Q-5).

Risks
- M68: nothing pre-set; the selector defaults and the fixed 7 % rate are M72 F12 (fix there, not again).

### CW-5 Orders from the panel

Exists
- `fno/OrderTicketV2.tsx:40-45,126-150` (bottom sheet on phone, side panel from md - already in place, not a page);
  basket `fno/BasketPanelV2.tsx` + `basket.ts` (browser storage); positions actions `fno_routes.py:597-667`
  (exit leg / group / part, reverse, modify resting).
- The manual-order origin (TT-2): set by the server on the ticket and basket routes only (`execution/approval.py:15-17`).
- AI reaches no order call (M72 review: only `monitor.execute` after a human approval).

Missing
- Status, fills and the position of the order placed from the panel shown in the same panel (today the ticket shows
  the result; the position lives on Positions).
- Prefill from the Copilot (M72 PR-9; values per M72Q-4).

Risks
- The ticket says "no mode in the body and no LIVE path" (`paper/fno_routes.py:1-6`); the frame "Practice mode
  (PAPER)" is always on (`OrderTicketV2.tsx:40`). CW-7 "LIVE alike" is met only by using this ONE path, so the LIVE
  manual path (TT-1) lights it up later; LIVE stays the owner's switch.

### CW-6 Trigger plans

Exists
- Resting book `execution/resting_book.py:1-60`: ENTRY / EXIT rows, `client_ref` unique per user (idempotent), statuses
  RESTING / FIRING / FILLED / REJECTED / CANCELLED / EXPIRED (`:27-42`), entries expire at the end of the day
  (`DAY_END`, `:37`), triggers PREMIUM / SPOT, TOUCH / CLOSE of his timeframe (`:50-56`, `trading/trigger_rules.py`).
- Trigger engine `execution/trigger_engine.py:1-37`: worker-only, tick pass + cycle, stops before targets before
  entries, claim RESTING -> FIRING once (`:171-177`), an order left FIRING settled from the broker by its client id
  (`:477-513`), entries wait while Redis is down (`:256-273`), heartbeat + stall notice; an entry fires through the
  ticket's own `place` so every check runs again; a LIVE entry is refused until TT-1 (`:26,434`).
- Approval: `execution/approval.py:1-35` (ENTRY proposal on `ai_actions`, Telegram buttons PW4, TTL and re-quote from
  operator settings `core/config.py:839-841`); exits never wait for a proposal.
- Broker GTT: no adapter implements it. Groww's "smart orders" are deliberately not used (`brokers/groww.py:31-33`,
  docs/design/BROKER_GROWW.md:27,52). DECISIONS 23 Oct (TM-23): own trigger engine by default; native GTT only as a
  per-user opt-in, cancelled first when any kill switch engages.

Missing
- A plan object (condition -> order(s) -> exits) with its own validity (an entry today dies at the day end), states
  Armed / Paused / Triggered / Done / Expired, edit / pause / cancel, list in the workspace.
- Conditions beyond price: "price inside an engine area + a candle of his timeframe closing with rejection", BOS /
  CHoCH on a timeframe, a time window. None is a trigger rule today.
- Multi-order fire (a basket) + exits attached after the fill (SX exists for the basket route, not for a fire).
- "Place at once" / "ask me first" as the user's own setting (no registry field).
- GTT in `BrokerInterface` (`brokers/base.py:144-330`: no `place_gtt` / `modify` / `cancel` / `list`).

Broker GTT offer (web, read 1 Nov 2026; segment support for F&O to be confirmed per broker before building)

| Broker | API | Shapes | Source |
|---|---|---|---|
| Upstox | v3 GTT (Beta) | SINGLE, MULTIPLE (2-3 rules: ENTRY / TARGET / STOPLOSS); community reports of a missing stop leg | https://upstox.com/developer/api-documentation/place-gtt-order |
| Zerodha | Kite Connect GTT | single, two-leg (OCO); place / modify / delete | https://kite.trade/docs/connect/v3/gtt/ (via the official PHP client docs) |
| Fyers | API v3 GTT | single, OCO (leg1 above, leg2 below the LTP); products CNC / MARGIN / MTF | https://fyers.in/api-docs/v3/api/trading/order-placement/gtt-orders/ |
| Dhan | v2 Forever Order | SINGLE, OCO; modify / delete | https://dhanhq.co/docs/v2/forever/ |
| Angel One | SmartAPI GTT rules | create / modify / cancel rule; a path mismatch in the Java SDK reported | https://smartapi.angelone.in/smartapi/forum/post/19178 |
| Groww | smart orders `/order-advance` | GTT, OCO (verified from the SDK in BROKER_GROWW.md) | repo doc |
| Kotak Neo, Shoonya, CoinDCX | not researched here | not known | - |

Risks
- A broker GTT fires at the broker: ATP's kill switch, risk checks, the PAPER lock and "ask me first" do not run at fire
  time. That is why TM-23 chose the own engine. M75 asks GTT for simple price plans - M75Q-1.
- Under Phase 0 a GTT IS a LIVE broker order (`platform/phase0_lock.py:86`): the GTT path is built and tested against a
  fake broker and stays off; PAPER plans always use ATP's engine with the same plan states.
- `approval_only_entries` (org flag) turns every automatic entry into a proposal (`approval.py:3-14`); a plan fire is
  automatic, not the TT-2 manual origin - with the flag on, "ask me first" applies whatever the plan says.
- Exits: a plan's exits are ordinary resting / group exits - never behind an approval or a kill switch (ADR-0004).
- C.4: the time window is an operational setting (allowed); no condition reads the chart by clock; the "15M" is the
  user's choice, not a preset.

### CW-7 PAPER = LIVE, multi-broker, journal

Exists
- One exit cadence PAPER / LIVE (`trigger_engine.py:5-8`); suites in docs/design/ORDER_PATH_MERGE.md (192 / 86).
- M45: `accounts/multi.py:1-12` (own accounts only), routing `accounts/fanout_plan.py:106-131`, sizing `:155-167`,
  ticket "Run on" (`OrderTicketV2.tsx:147-150`).
- Journal hooks `journal/capture.py:1-12,437-476` (`on_entry` / `on_exit` / `on_stop_change`), sources `:62`.

Missing
- Journal source "PLAN" (`capture.py:62` has MANUAL, STRATEGY, SCAN, PROPOSAL, WEBHOOK, API).
- A plan routed to several own accounts (one fire per routed account, one GTT per account).

Risks
- `fanout_plan.size` uses MULTIPLIER x 1 for an account without a setting (`fanout_plan.py:157`) - a pre-chosen value in
  M68's sense; add it to the TS1 audit, the plan asks instead.

### CW-8 Tests + proof

Exists: exits / PAPER = LIVE suites, trigger rules parity (`tests/test_tm2_trigger_rules.py`), the lightweight fake
(`chart/lightweightFake.fixture.ts`), synthetic engine provider (`engine_provider/loader.py:127-133`).
Missing: no-navigation test (other agent), plan fires once + exits follow, GTT with a fake broker, restart while armed,
phone + desktop screenshots, a downloaded PNG / PDF.

## 2. Findings (W1..W21)

- **W1 No chart in the Copilot.** "Read the chart" holds Market Pulse only (`CopilotFoldedV2.tsx:41-42`); the
   workspace is new UI, reusing `EmbeddedChartV2` / `ChartV2` / `useReadChart` (no second chart stack).
- **W2 Area = two dashed price lines with axis titles** (`model.ts:275-279` -> `chartCore.ts:434-436`). Fix: areas
   as `LayerData.bands` through `engine.setLayer` (the `readLayer` way); price lines only for single levels the user
   owns; `axisLabelVisible` with a short value tag, never a title on the axis.
- **W3 Label collisions.** Band labels at the right edge (`geometry.ts:225`) under the floating view controls
   (`ChartViewControls.tsx:38-41`). Fix: a label placer that knows the controls' rectangle and the axis inset, places
   labels at the left inside the band, stacks / skips overlapping labels, with a pure test over the shapes.
- **W4 Contract gaps** for trend per TF, BOS / CHoCH, test count, divergence, next areas: interface 2 (optional
   fields, 1 still accepted); until the engine lands the workspace reads ATP's layers; never mix the two in one reading
   (the source is labelled).
- **W5 No request-time engine read.** Add a cached read per (symbol, TF, last closed bar) for `trade_engine` users,
   from the same data adapter as the worker (`engine_provider/sources.py:198`) - no look-ahead, same result as the worker.
- **W6 State vocabulary** differs (three sets); show only states that have a public phrase (M75Q-4).
- **W7 Invalidation level** is internal by contract (M75Q-3).
- **W8 Vision on an un-uploaded chart.** A hybrid run from ATP's rendered chart + facts (no item); one run per
   (symbol, TF, closed bar) cached; cost under M72 F1 / F2 (M75Q-5). Every price on the card from the facts; the M72
   grounding check on every line.
- **W9 F&O symbols and bars.** Picker excludes F&O (`SymbolPicker.tsx:87-92`); contract bars are NFO daily only.
- **W10 Export.** No workspace PNG / PDF; use the chart canvas + layer for PNG (browser), Pillow for a one-page PDF
    on the server from the server PNG (`reading_png.py`) + the same table rows - no new dependency.
- **W11 Two tickets, no target / trailing / time at entry** on the single ticket; SX has no time exit.
- **W12 Broker margin** exists for Upstox / Zerodha only; elsewhere the labelled estimate stays.
- **W13 PAPER-only routes.** CW-7 is satisfied by using the one ticket path; nothing new may have a LIVE branch of
    its own (`fno_routes.py:1-6`).
- **W14 No plan object**; the resting book is single-order and DAY-only - a plan table above it, firing into the
    ticket's `place` / basket execute, reusing `_claim` and the client-id settle for restart safety.
- **W15 Method conditions** need closed-bar evaluation in the worker per plan TF (no look-ahead, armed-after rule as
    `trigger_rules.closed_candles`); "rejection" reuses an existing reader, never a new rule (M75Q-4).
- **W16 GTT vs TM-23** (M75Q-1); **W17** GTT under Phase 0 = LIVE: off, fake-broker tested; kill switch cancels GTTs
    first; a GTT fill is reconciled into a trade + exits by the worker (by the plan's client id).
- **W18 Approval for plans.** "Place at once / ask me first" is a new Not set field; `approval_only_entries`
    overrides; TTL / re-quote today are operator values (M75Q-2).
- **W19 Journal source PLAN** + plan id on the entry; M45 one fire per routed account; MB-2 multiplier to TS1.
- **W20 Order-path PRs** (plans, GTT, panel exits) merge one at a time per ORDER_PATH_MERGE.md and extend both suites.
- **W21 ADR-0006.** The Copilot may PREFILL a plan like a ticket (M72Q-4 values only); arming, editing and cancelling
    are the user's taps; no AI tool writes a plan row.

## 3. PR plan (sensible order; R2 max 5 build items in progress)

Every PR: `ui_v2` + its flag, phone 390 + desktop screenshots on ONE comparison page per milestone, deploy line +
migration yes / no, tests named below. "Order-path" = merges one at a time per docs/design/ORDER_PATH_MERGE.md
(branch `claude/inbox-m71-pl7`), with the exits-never-blocked and PAPER = LIVE suites run and extended.

| PR | Scope | Files (main) | Flag | Migration | Order-path | Tests / CW-8 proof |
|---|---|---|---|---|---|---|
| N | No-navigation conversions (other agent's inventory) | per inventory | `ui_v2` | no | no | every listed button keeps the URL path; deep links open directly |
| W-1 | Chart label / band fix (W2, W3) everywhere the levels draw areas | `copilot/chat/model.ts`, `AnswerV2.tsx`, `chart/chartCore.ts`, `charting/geometry.ts`, `chart/ChartViewControls.tsx` | none (fix) | no | no | shape test: no label inside the controls rect or the axis inset; areas as bands; before / after screenshots |
| W-2 | Workspace shell (CW-1): chart full-width on phone, picker incl. F&O contracts, TF dropdown, auto code reading on open, analysis side panel / bottom sheet | `copilot/chat/CopilotPageV2.tsx`, new `copilot/workspace/*`, `ui/SymbolPicker.tsx` (F&O universe opt-in), `charts/read_chart.py` (reused) | new `copilot_workspace` | no | no | open = reading starts once; symbol / TF change re-reads; contract without bars says so; 360 px no overflow |
| W-3 | Annotations (CW-2): one reading response = areas (state, tests, TF), swings HH / HL / LH / LL, trend per TF, BOS / CHoCH, next areas, divergence; engine source when `trade_engine` (W4, W5), ATP layers otherwise | `charts/read_chart.py` (extend, keep), `engine_provider/contract.py` (interface 2, optional), new cached engine read | `copilot_workspace`, `trade_engine` | no | no | no look-ahead; same reading as the worker for the same bar; no engine token / model id in any response (synthetic provider) |
| W-4 | Vision card (CW-2): hybrid from ATP's rendered chart + facts, cached per closed bar, short card + table (W8) | `chart_analyze/hybrid.py`, `service.py`, `reading_png.py`, new route | `chart_analyze_hybrid` + `copilot_workspace` | no (cache in the existing items store or Redis; stated in the PR) | no | every price on the card is in the facts; advice filter; cost per answer posted; cap hit = code card only |
| W-5 | Export (CW-3): PNG from the chart + layer, one-page PDF (chart, table, explanation, IST time, "information only") | `copilot/workspace/export.ts`, new `charts/workspace_pdf.py` (Pillow) | `copilot_workspace` | no | no | downloaded PNG + PDF attached to the proof page; PDF = one page; deterministic bytes for a fixed input |
| W-6 | Trade plan panel, previews only (CW-4): legs / future / equity, payoff, Greeks, max loss / profit, break-evens, margin (broker where offered, else labelled estimate), exits-at-entry form (SL, target, trailing, time, CX / SX) from M68 fields | `copilot/workspace/PlanPanel.tsx` reusing `optionsBuilder/*`, `fno/*`; preview routes reused | `copilot_workspace` | no | no (no place call) | every field Not set until chosen; numbers equal the builder's for the same legs; phone sheet |
| W-7 | Place from the panel (CW-5): ticket / basket inside the panel, status, fills, position, modify / exit there; target / trailing / time at entry on the single ticket (W11) | `fno/OrderTicketV2.tsx`, `paper/fno_routes.py` `OrderBody`, `paper/fno_ticket.py`, `execution/strategy_exits.py` | `copilot_workspace` | no (exits use existing rows; stated in the PR) | **yes** | no Place without the user's tap; exits suite + PAPER = LIVE suite pass and extended; journal entry written |
| W-8 | Trigger plans core (CW-6, CW-7): `trigger_plans` table (condition, orders, exits, validity, approval choice, states), price cross / touch + time window, fire via ticket / basket once (claim + client id), exits attach after the fill, ask-me-first via ENTRY proposal (in-app + Telegram), M45 routing, journal source PLAN, plans list (Armed / Paused / Triggered / Done / Expired) | new `execution/trigger_plans.py`, `execution/trigger_engine.py` (call), `journal/capture.py:62`, `execution/approval.py` (origin "plan"), `me/trade_settings.py` (new field) | new `trigger_plans` | **yes** (new table; rollback-safe down) | **yes** | fires exactly once on a double pass; exits follow the fill; restart while armed (FIRING settled, no second order); approval-only org forces a proposal; exits never wait |
| W-9 | Method conditions: price inside an area + a closed candle of his TF with the chosen reader's rejection; BOS / CHoCH on a TF | `execution/trigger_plans.py`, worker closed-bar pass, readers reused | `trigger_plans` | no | **yes** | no look-ahead (armed-after rule); parity with the backtest path; engine vs ATP source labelled |
| W-10 | Broker GTT path (M75Q-1): optional `BrokerInterface` GTT methods, Upstox + Zerodha first, then Fyers / Dhan / Angel; kill switch cancels GTTs first; GTT fill reconciled into a trade + exits | `brokers/base.py`, `brokers/upstox.py`, `brokers/zerodha.py`, `kill_switch/*`, `execution/trigger_plans.py` | new `broker_gtt` (OFF; LIVE only) | yes only if GTT ids need columns beyond W-8 (planned into W-8) | **yes** | fake broker: create / modify / cancel / fill / reject; kill switch cancels first; Phase 0 lock refuses the create |
| W-11 | Proof page (CW-8): phone + desktop screenshots, the PNG / PDF, test counts | docs/proof page | - | no | no | one comparison page for the owner's ✅ |

Order: N and W-1 first (independent), then W-2 -> W-3 -> W-4 -> W-5 -> W-6, then the order-path chain W-7 -> W-8 ->
W-9 -> W-10 one at a time, W-11 last. W-3 waits for nothing (ATP layers); its engine part lights up when the M66
snapshot lands. W-6 / W-7 build on M68 PR-3 / PR-4 fields and M72 PR-9 (prefill).

## 4. Owner decisions

Rows M75Q-1..5 in docs/OPEN_QUESTIONS.md (each with a provisional default): Q-1 broker GTT vs TM-23, Q-2 place at
once / ask me first, Q-3 invalidation levels, Q-4 area states and "rejection", Q-5 cost of the automatic AI analysis.

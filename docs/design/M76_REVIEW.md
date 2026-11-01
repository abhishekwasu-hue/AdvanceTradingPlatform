# M76 review: Copilot workspace facilities + mistake-proofing against the code (CLAUDE.md §6, before any build)

Order: ATP_ORDER_M76.md + amendment (1)-(5) (on `claude/inbox-m71-pl7`). Extends M75 (M75_REVIEW.md on
`claude/m75-review`: W1..W21, PRs N, W-1..W-11, M75Q-1..5). Base `origin/main` f6b795b0.

Amendment (1)-(5) as read here: a warning compares only with his OWN saved rule (none = it does not exist) or a plain
fact; one switch per type (Settings › Trade settings › Warnings); ON only for the five pure mistakes, structure /
strategy ON as information with no click unless "ask me"; "Don't show again" everywhere; one line of facts; background
checks, Place never disabled; ONE card, worst first, 3 lines + "more", no pop-up, no repeat in a session, accepted
once per instrument and day = hidden that day, never over Place / price fields, 300 ms budget. Exit confirms are not
warnings; exits never get one (ADR-0004).

## 0. Summary

- Today's tickets mix three kinds of text in up to five separate blocks: errors / refusals (blocks: exchange facts,
  kill switches, M64 own limits), warnings (strings) and "Before you place" risk lines. None has a type id, a switch,
  "Don't show again", a suppression store or a journal record.
- Three existing pre-trade texts compare with ATP's (or Trade's) own idea, not his saved rule: the equity ticket's
  fixed checklist ("discipline miss"), the liquidity line's chain-quartile comparison, the portfolio one-sided warning.
- Missing: duplicate, market state, MIS cut-off, limit vs LTP; W-1 needs M75 W-3. E-2 and S-1 maths mostly exist.

## 1. A - Pre-trade check card (W-1..W-4)

### Exists today (every pre-trade text, classified)

F&O ticket `backend/app/paper/fno_ticket.py:414-556` (`assess`, read-only; the frontend's Review step):
| Text (file:line) | Kind | Source of truth | Amendment |
|---|---|---|---|
| "This expiry has passed" `:443-444` | error | fact | ok (exchange fact; stays a block) |
| "This contract expires today." `:445-446` | warning | fact (expiry day) | ok - becomes card type `expiry_day` |
| Tick-size errors `ask_errors` `:203-221` | error | fact (tick) | ok (exchange fact) |
| Stop required on a sold option `:449` (`own_limits.stop_required_on_sold`) | error | own rule | ok (M64 own limit) |
| "No live price just now ... chain" `:459` | warning | fact | ok - `price_source` line |
| "Waits for your price ..." `:473` | warning | fact (his own choice) | not a warning: a status line |
| F&O ban note `:496` | warning | fact (exchange list) | ok - `fo_ban` |
| Guardian / Risk notes `:500-502`, `RiskManager` warnings `:512` (`risk_manager.py:130,160`) | warning | own rule (M64) | ok wording; they are notes on his own limits |
| Risk per trade block `:514-523` | refusal (block) | own rule (M64 RM-3) | ok - M64 limits are his saved hard limits, not M76 warnings |
| Experience gate `:490` (`paper/experience.py:1-25`) | refusal | ATP thresholds (TS1-057) | own switch OFF by default (M64 row 14); the numbers are ATP's - M68 TS1 fix, not M76 |
| Broker margin `:530` (`_broker_margin`, a broker network call) | figure | fact | ok; slow (finding M76-6) |
Lot multiple: the F&O ticket takes LOTS (`OrderAsk.lots`), so a non-multiple cannot be typed there; units = lots x
master lot size (`:450-451`). Duplicate, market state, MIS cut-off, limit-vs-LTP: none in `assess`.

Equity ticket `backend/app/paper/ticket.py:125-235`:
- Fixed checklist `:57-60` (trend aligned, level identified, pullback confirmed, commitment candle, no event, liquidity
  OK) and `:214-215` "Checklist not complete: ... it is counted as a discipline miss" - ATP's list and a judgement.
  His own list exists: `journal/settings.py:65-66` `checklist_rules` (M62 JN-2, "none until he adds them").
- Fixed setup list `SETUPS :53-56` ("Setup must be one of") - ATP's list as a required field.
- Lot multiple `:189-190` "Quantity must be a whole number of lots of {lot}" - `lot = cfg.lot_size` (`:173`), the
  organisation's unit for cash underlyings, not an exchange lot (NSE cash lot = 1 share).
- Over-cap override `:191-208` and R:R minimum `:187-188` - his own cap / minimum (TT-12); ok.

Frontend surfaces (F&O ticket `frontend/src/fno/OrderTicketV2.tsx`):
- Two taps: "Review" `:476-480` (server preview; replaces the fields `:455`), then Place `:500-503` (disabled only by
  errors / refusals - ok).
- Up to five blocks on the Review step: RiskChecksV2 "Before you place" `:567-568` (risk, events, liquidity, margin
  lines), refusals `:569-573`, warnings `:575-579`, smart slices `:574`, block message / "blocked by" `:484-495` with a
  `<a href>` "Change limit" `:493` (a navigation away - M75 rule 1 inventory).
- Liquidity line `frontend/src/fno/riskParts.ts:85,90-99,182-198`: with no own limits it flags a leg "wider than most
  strikes on screen" (top quarter of gaps) / "among the lowest" OI (bottom quarter) in warn tone - ATP's comparison.
  With his own limits (`settings/liquidityLimits`, FNO-8B-28) `:134-150,172-180` it compares with his numbers - ok.
- Event line `riskParts.ts:29-38` and margin line `:207-218`: facts - ok.
- Basket `frontend/src/fno/BasketPanelV2.tsx`: RiskChecksV2 on edit `:550` and confirm `:757`, per-leg issue lists
  `:723`, refusals `:760-763`; server `paper/fno_basket.py:211-242`.

Other pre-trade texts:
- Portfolio `backend/app/portfolio/risk.py:140-146` one-sided warning: "one large move against them hits them together
  (correlated risk, no diversification)" - Trade's rule (2+ directional, all one side) and a judgement clause.
- Proposal panel `proposals/RiskCheckPanel.tsx:11-30`: strike breach chance (ATP model, `strike_breach.py:1-20`) -
  labelled estimate; never in the card. Settlement warning `fno_routes.py:679`, own switch `trade_settings.py:231` - ok.

Exits (not warnings; listed for the friction rule):
- One confirm each: `trading/PositionsV2.tsx:124`, `pages/PositionsPage.tsx:89`, `fno/ChainTradingV2.tsx:119`
  (browser `window.confirm`), `trading/ExitAllV2.tsx:77`, `terminal/PanelsV2.tsx:311` (in-app ConfirmDialog).
- Exit-leg of a hedge whose sold leg is open: a server WARNING that needs a second tap
  (`paper/fno_position_actions.py:98-102,150-152`, route `fno_routes.py:597-604`, UI `PositionActionsV2.tsx:28-31,78`).

### W-1 Structure conflict
Exists: `charts/read_chart.py:1-21` (direction, protected level, areas per TF), `zones/lifecycle.py:40-60`, contract
`engine_provider/contract.py:150-289` (state token, `HigherTimeframeSide`, internal invalidation). Gaps = M75 W4-W7
(trend per TF, BOS / CHoCH, next areas, state words, M75Q-3 / Q-4). W-1 rides on M75 W-3's reading: TF trend vs setup
side, area side vs setup side, room in points to the next opposite area of the same TF. One line per TF (Z-1u).

### W-2 Fat-finger and consistency
Exists: tick checks, lots-based quantity, margin estimate / broker margin, F&O ban, expiry passed / today, M64 limits,
idempotent `client_ref` per ticket open (`OrderTicketV2.tsx:165`, `fno_routes.py:118`) - a double tap is one order.
Market state reader exists but the ticket does not use it: `market_data/strip_routes.py:52,63-80` (pre-open 09:00-09:15,
holidays). Broker square-off time is a registry fact (`me/trade_settings.py` `broker_square_off`, `square_off_time`).
Missing: duplicate (open / resting, same instrument + side); limit far from LTP (`order_safety.py:25-50` is MARKET
protection only); MIS near cut-off; usual size; side / expiry / strike vs plan or own rule (no plan until M75 W-6 /
W-8; `expiry_rule`, `strike_rule`, `strike_offset`, `option_position` exist in the registry, not all `wired`).

### W-3 Risk preview
Exists: basket max loss / profit, break-evens, Greeks (`fno_basket.py:124-144`, `payoff.py:13-66`), hedge margin
(`:79-96`), book Greeks `trading/routes.py:186,275`, exposure `portfolio/routes.py:37`. Missing: after-trade totals,
points beside ₹, margin after vs available, IV row - margin under an IV rise is not knowable (M76Q-5).

### W-4 Journal + coach
Exists: `JournalEntry` `db/models.py:469-489` (`plan_json`, `checklist_json` = his own rules yes / no); capture hooks
`journal/capture.py:437-476`; coach `ai/coach.py:1-31` (behaviour flags with ATP thresholds, e.g. `REVENGE_MINUTES = 15`
`:31`, and "one concrete fix" each). Missing: accepted warnings on the entry; a coach section "warnings you accepted
and their outcomes" (numbers only, no fix line). No migration needed: `plan_json["accepted_warnings"]`.

## 2. B - What-if and scenarios (S-1..S-3)

- S-1 exists: `options_builder/model.py:100-109` (T+0 / any date, `iv_shift`), `routes.py:86-87,236-241`. Missing:
  spot ±N and combined overlays, a Greek line, saved scenarios (prefs), the whole book through `value_curve`. Fixed 7 %
  rate (`core/config.py:462`) is M72 F12.
- S-2 missing: P&L of open + planned legs at the engine's / ATP's next areas = `value_curve` evaluated at the area
  bounds (both edges, never a single price for public surfaces; his own chart may show the range, CLAUDE.md §3).
  Margin at those levels: only the labelled estimate (no SPAN).
- S-3 exists: market events (`risk_engine/guardian.py:17-21`, `market_events`), the ticket's event line (`riskParts.ts:
  29-38`, `/paper/fno/event-risk`), expiry checklist (`expiry_checklist/*`), rhythm calendar (`rhythm/build.py:7`).
  Missing: events drawn on the chart, his own event list (own list, prefs), weekly expiry day marker from the master's
  expiries (fact). Information only; Guardian M8 event blocks stay his M64 switch (`event_rules`, OFF).

## 3. C - Execution helpers (E-1..E-4)

- E-1 defaults: M68 registry `me/trade_settings.py:120-330` (order / size / exits / options sections), ticket asks
  for Not set fields (`fno_ticket.py:552-554` `missing`, `OrderTicketV2.tsx:168-178`). Helpers that exist: reverse
  `fno_routes.py:655`, roll preview `:514` (`fno/RollV2.tsx`), exit leg `:597`, exit part `:644`. Missing: "mirror
  this spread", "add hedge" (prepare only, legs from his `hedge_offset` or Not set and asked). All prepare the ticket.
- E-2 exists: hedge first `fno_basket.py:60-64`, sold legs first on exit `:296,338-340`, stop at the first unfilled leg
  `:313-317`, INCOMPLETE with his rule (`incomplete_structures.py:1-25`), re-price loop (`marketable_limit.py:90`).
  Missing: per-leg partial fills in the workspace; a "new LTP, your tap" helper (M76-16).
- E-3 exists: `fno/positionInsights.ts:1-15`, Greeks route. Missing: cards in the workspace, spot -> short strike in
  points / %, time to expiry. No price-line drag exists (builder strike drag only, `optionsBuilder/model.ts:280`);
  "FNO-4.10" is not in docs/ - drag is new, a modify via `fno_routes.py:667` with a confirm.
- E-4 exists: exit group `fno_routes.py:607`, exit part `:644` (close half = lots / 2, his tap), CX `:323-410`, SX
  `execution/strategy_exits.py:1-20`, break-even rule `trading/exit_rules.py:9,33,59`. Missing: one-tap "move SL to
  break-even" on a position (a modify of the resting stop), and these buttons on the chart.

## 4. D - Attention and automation (D-1..D-3)

- D-1 exists: `alerts/engine.py:1-20` (dedupe, cooldown, cap), rules `rules_routes.py:40-52`, zone functions
  (`barclose.py:58`), Telegram + in-app, own-level position alerts (8b-f). Missing: rule kinds
  "short strike within N", "IV spike over his %", "margin use above his %", "time reminder before his cut-off",
  "candle rejection at an area" (reader choice per M75Q-4); "Open here" deep link into the workspace (no navigation).
  Every N / % is his; none set = the card is not offered.
- D-2 missing: depends on M75 W-8 / W-9 (no plan object). The dry run replays closed bars of the last N sessions
  through the same rule code (no look-ahead, armed-after rule), read-only.
- D-3 exists: rhythm digests `rhythm/build.py:1-18` (his day, positions, limits). Missing: a close summary inside the
  workspace with accepted warnings (W-4) and plan adherence = his own checklist_rules answers + plan vs fill; numbers.

## 5. E - Learning and recall (L-1, L-2)

- L-1 exists: `setup_recall/service.py:1-22` matches setup / side / holding / timeframe, closed strictly before entry.
  Missing: "same area / structure" - no area context is stored at entry; add it to `plan_json` at capture (area TF,
  side, state phrase) and match on it only for entries captured after that (no back-fill guess).
- L-2: public phrases only - `frontend/src/ui/glossary.ts:1-4,77` (one glossary, no advice). The method's own text
  lives in atp-private; the contract already says ATP shows only states with a public phrase (`contract.py:168`).
  A tap shows the glossary line for the annotation kind; a private phrase only through the provider, never copied.

## 6. F - Tests today
Exits / PAPER = LIVE suites (192 / 86), `fno/ticketLook.test.tsx`, e2e `chain-ticket-v2`, `basket-v2`, `terminal-v2`,
`fno-workspace-v2`. No client timing / card-count instrumentation; server route latency exists (`middleware.py:12,73`).

## 7. Findings (M76-1..M76-16)

- **M76-1 Equity ticket checklist is ATP's, with judgement wording** (`paper/ticket.py:57-60,214-215`, UI
  `components/PaperTicketCard.tsx:114-117,182`). Violates (1) and (3). Fix: his own `checklist_rules`
  (`journal/settings.py:65-66`); none saved = no checklist line; wording "Your checklist: 4 of 6 ticked". M76Q-3.
- **M76-2 Liquidity quartile comparison** (`riskParts.ts:85,97-99,186-197`) without his limits. Violates (1). Fix:
  facts only (bid, ask, gap %, OI) with no comparison; a warning only against his own limits. M76Q-3.
- **M76-3 Portfolio one-sided warning** (`portfolio/risk.py:140-146`): Trade's rule + "no diversification" clause.
  Violates (1) / (3) if shown pre-trade. Fix: never in the card; on Portfolio as a count only. M76Q-3.
- **M76-4 Fixed setup list required** (`ticket.py:53-56,137-138`) - ATP's list as a mandatory field. Fix: his own setup
  list (journal own lists) or free text; not a warning, recorded in M68 TS1 terms. M76Q-3.
- **M76-5 Five blocks, not one card** (`OrderTicketV2.tsx:484-495,567-579`; basket `:550,723,757,760`). Violates (5).
  Fix: one `CheckCard` (worst first, 3 lines + "more"), refusals stay a separate block (they are blocks, not warnings).
- **M76-6 The preview waits for slow reads** - broker margin network call (`fno_ticket.py:530`), price, guardian /
  risk reads in one sequential `assess`; Place waits for the whole preview. Fix: blocks (refusals) stay in the preview;
  M76 checks and the broker margin go to a separate background check call with a 300 ms budget.
- **M76-7 No type ids, switches, "Don't show again" or suppression** for any warning (plain `List[str]`). Fix: a
  registry of warning types (section 9) and structured findings `{id, severity, facts, text}`.
- **M76-8 Duplicate, market state, MIS cut-off, limit-vs-LTP are not checked** (W-2 table above). New fact checks.
- **M76-9 Equity "lot" is the organisation's unit** (`ticket.py:173,189-190`), not an exchange lot. The lot-multiple
  check must use the instrument master's lot (F&O) and 1 for cash; the org unit stays an M68 TS1 item.
- **M76-10 M68 vs defaults.** The ON defaults are the OWNER's (amendment 2), marked `default_by: owner`; every
  threshold stays Not set (M76Q-1).
- **M76-11 Exit friction.** The hedge exit-leg warning adds a second tap (`fno_position_actions.py:150-152`). M76 adds
  none; browser `window.confirm` exits (`PositionsV2.tsx:124`, `PositionsPage.tsx:89`, `ChainTradingV2.tsx:119`) are
  UI-28 U3.3 work. M76Q-4.
- **M76-12 IV-shock margin and invalidation-vs-SL have no source of truth** (no SPAN in PAPER; broker margin APIs take
  no IV; `Signal.invalidation` internal, M75Q-3). M76Q-5.
- **M76-13 "Usual size"** exists only when he saves N (x his median lots) and M (last trades); else not at all.
- **M76-14 W-1 depends on M75 W-3**; until then ATP-layer facts only, labelled.
- **M76-15 "Change limit" navigates away** (`OrderTicketV2.tsx:493`, `ticketModel.ts:199-205`) - into M75 PR N.
- **M76-16 Re-price is automatic** in his band (`marketable_limit.py:90`); E-2's confirm helper is for an INCOMPLETE leg
  after that loop, no rewrite (§8).
Housekeeping: docs/OPEN_QUESTIONS.md is 109 KB (rule ≤ 30 KB) - a separate docs PR should move closed rows out.

## 8. Card, suppression, 300 ms budget, metrics (amendment 5)

Card (`frontend/src/fno/CheckCardV2.tsx`, one component for ticket, basket, plan panel):
- One card in the ticket's scroll area between the fields and the sticky action bar; never `fixed`, never over
  Place (sticky bar `OrderTicketV2.tsx:470-475`) or the price / quantity fields; no modal, no toast.
- Lines sorted: pure mistakes, then facts, then structure / strategy; 3 visible, "N more" expands in place. Each line:
  one fact sentence + "Don't show again" (sets the type's switch OFF, undo in Settings) + "Edit" (focuses the field).
  "Accept and continue" = pressing Place (no extra tap) unless the type's "ask me" is ON (M76Q-4).
- Refusals / errors (blocks) stay outside the card (they are not warnings; Place disabled only by them, as today).

Check service (`backend/app/paper/pretrade_checks.py`, route `POST /api/paper/fno/order/checks`, read-only):
- Input: the draft (ticket / basket / plan legs) + `client_ref`. Each check is an `async` function with its own
  timeout; `asyncio.wait(timeout=0.3)`; returns ready findings + `pending: [ids]`; one follow-up request for pending ids
  (cached results, keyed by draft hash). Called on draft change (debounced) and on Review; Place never awaits it.
- Cached: reading per (symbol, TF, closed bar) (M75 W5), broker margin per draft hash.
- Suppression: (a) "accepted for this instrument today" - Redis `pretrade:acc:{user}:{type}:{instrument_key}` with TTL to
  the end of the IST day (operational, C.4), written when Place is pressed with that line shown; (b) "same fact this
  session" - in-memory set in the ticket store (tab session), keyed by fact hash; (c) "Don't show again" - the switch
  in trade_settings (prefs store, no table). No migration.

Metrics:
- Events: client `POST /api/ui/ticket-metrics` (no PII: surface, cards_shown, lines_total, lines_visible, ms_open_to_place,
  ms_review_to_place, checks_ms, pending_at_place) -> Prometheus histograms / counters via `observability/metrics.py:50`.
  Server: check-call latency by route template exists (`middleware.py:73-74`).
- Baseline (MP-0, today's ticket): Playwright over the four ticket e2e flows counts warning surfaces per ticket
  (`[data-ticket=warnings] li`, `[data-ticket=refusals]`, `[data-risk-part=warn]`, dialogs) and times open -> Place
  (mocked API = UI cost; preview p50 / p95 from the route histogram on the real site). After = same flows; both on the
  proof page.

## 9. Warnings catalogue (Settings › Trade settings › Warnings; keys `warnings.<id>`, `warnings.<id>_ask`)

Default: ON-M = ON, pure mistake; ON-I = ON as information, no click; - = does not exist until he saves the rule.
| id | Source of truth | Default | Needs his value | Wording example (one line of facts) |
|---|---|---|---|---|
| `lot_multiple` | fact: master lot size (equity / API qty paths) | ON-M | - | "Quantity 80 · lot size 75 · not a multiple" |
| `duplicate_order` | fact: his open position / resting order, same instrument + side | ON-M | - | "Open: NIFTY 24500 CE buy 2 lots · this order: buy 2 lots" |
| `expiry_vs_plan` | own plan / `expiry_rule` | ON-M | plan or rule | "Your plan: 06 Nov expiry · this order: 13 Nov" |
| `limit_far_from_ltp` | fact LTP + his % | ON-M | `warnings.collar_pct` | "Limit 182.00 · LTP 160.40 · 13.5 % away · your band 5 %" |
| `margin_short` | fact: margin vs available | ON-M | - | "Margin 1,12,400 · available 96,800 · short 15,600" |
| `market_state` | fact: session / holiday | ON-I | - | "Market: pre-open (09:00-09:15)" |
| `expiry_day` | fact | ON-I | - | "This contract expires today" |
| `mis_cutoff` | fact: broker square-off + his minutes | ON-I | `warnings.mis_minutes` for "near" | "MIS · broker square-off 15:20 · 12 min left" |
| `fo_ban` | fact: exchange list | ON-I | - | "BANKNIFTY: in the F&O ban list today" |
| `event` | fact: his events calendar | ON-I | - | "Before the 06 Nov expiry: RBI policy on 05 Nov" |
| `trend_conflict` | engine / ATP read, per TF | ON-I | - | "Daily trend: down · your setup: bullish" |
| `area_conflict` | engine / ATP area | ON-I | - | "Price inside a seller area (1H, 24,480-24,560) · your setup: bullish" |
| `strike_in_area` | engine / ATP area | ON-I | - | "Short 24,500 PE inside a buyer area (Daily, 24,420-24,520)" |
| `room` | next opposite area, same TF | ON-I | - | "Next seller area (1H): 62 points above" |
| `false_break_entry` | engine state with a public phrase | ON-I | M75Q-4 | "Entry area state: false break (1H)" |
| `counter_trend` | engine BOS / CHoCH | ON-I | M75 W-3 | "Daily trend: up · no CHoCH on 1H · your setup: bearish" |
| `side_vs_plan` | own plan / `option_position` | ON-I | plan or rule | "Your plan: sell a PE spread · this order: buy PE" |
| `strike_vs_rule` | own `strike_offset` / `strike_rule` | ON-I | his rule | "Your rule: 2 strikes from ATM · this strike: 5" |
| `usual_size` | his history + his N, M | - | N and M | "4 lots · your median 1 lot (last 20) · your limit 3x" |
| `liquidity_limits` | his liquidity limits | ON-I | his limits | "Bid-ask gap 3.1 % · your limit 2 %" |
| `checklist` | his `checklist_rules` | ON-I | his list | "Your checklist: 4 of 6 ticked" |
| `iv_shock` | builder model + his X % | - | `warnings.iv_rise_pct` | "IV +5 %: MTM -4,200 (model) · margin not known" |
"Ask me" (`_ask`) is OFF for every type by default. Risk preview (W-3) rows are figures, not warnings (no switch).
Left out on purpose: strike breach chance, one-sided book, chain quartiles, experience level, coach flags.

## 10. PR plan (after the M75 PRs N, W-1 bands, W-2 workspace shell, W-3 annotations ... W-11)

Every PR: `ui_v2` + its flag, phone + desktop screenshots (one page per milestone), deploy line, migration. Order-path
= one at a time per ORDER_PATH_MERGE.md, both suites extended. New flag `trade_warnings`; `copilot_workspace` from M75.

| PR | Scope | Flag | Migration | Order-path | Tests |
|---|---|---|---|---|---|
| MP-0 | Baseline metrics (`/api/ui/ticket-metrics`, Playwright counts / timing) on today's ticket | none | no | yes (ticket UI) | no UI change |
| MP-1 | Warnings section in the registry (switch + ask per type, thresholds Not set), suppression store | `trade_warnings` | no (prefs + Redis) | no | defaults per catalogue; no threshold pre-filled; TTL |
| MP-2 | Check service + ONE card (ticket / basket); fact checks; existing strings folded in; broker margin in background; M76-1..4 fixes | `trade_warnings` | no | **yes** | fixture per type; Place never disabled; slow fake check -> Place proceeds, card fills after; 3 lines |
| MP-3 | Structure conflicts W-1 from M75 W-3 reading (engine when `trade_engine`, ATP layer else, labelled) | `trade_warnings`, `copilot_workspace` | no | yes (ticket card) | no look-ahead; per-TF lines never merged; no engine token in a response |
| MP-4 | Risk preview W-3: after-trade totals, ₹ + points, margin after, exposure same underlying, IV row (M76Q-5) | `copilot_workspace` | no | no (read-only) | numbers equal builder / Greeks route for the same legs |
| MP-5 | Accepted warnings to journal (`plan_json.accepted_warnings`), coach section numbers-only | `trade_warnings`, `discipline_engine` | no | **yes** (place passes ids) | journal record per accepted type; outcome counts; no fix text |
| MP-6 | What-if S-1 / S-2 (overlays, Greek line, saved scenarios, book, "price reaches area X") | `copilot_workspace` | no | no | maths vs `value_curve` |
| MP-7 | Events S-3 on chart + card; own event list | `copilot_workspace` | no | no | info only; Guardian M8 untouched |
| MP-8 | E-1 helpers: mirror / reverse / roll / add hedge / close leg - prepare the ticket only | `copilot_workspace` | no | **yes** | no field filled outside his settings / the object; no place call |
| MP-9 | E-2 per-leg partial fills + re-price helper for an INCOMPLETE leg (new LTP, his tap) | `copilot_workspace` | no | **yes** | hedge-first / short-first suites; nothing sent without his tap |
| MP-10 | E-3 / E-4 position cards, SL / target drag (confirm), close all / half, SL to BE, CX / SX edit | `copilot_workspace` | no | **yes** | no new step on any exit |
| MP-11 | D-1 watch cards (opt-in, his N / %), Telegram + in-app, "Open here" | `copilot_workspace` | no (rule text; PR states it if a column is needed) | no | nothing places |
| MP-12 | D-2 dry run of trigger plans (after M75 W-8 / W-9) | `trigger_plans` | no | no (read-only replay) | parity with the plan engine; no look-ahead |
| MP-13 | D-3 close summary in the workspace | `copilot_workspace` | no | no | numbers only; his own checklist |
| MP-14 | L-1 recall by area context (captured at entry) + L-2 teach-on-chart (glossary / provider phrase) | `copilot_workspace` | no | no | closed-before-entry only; no private text in public files |
| MP-15 | Proof: screenshots (phone + desktop) of card, what-if, position cards, watch card; metrics before / after | - | no | no | one comparison page |

Order: MP-0 first (before any card change, so the baseline is today's ticket), MP-1, then order-path MP-2 after M75
W-7 merges; MP-3 after M75 W-3; MP-4 / MP-6 / MP-7 after M75 W-6; then MP-5 -> MP-8 -> MP-9 -> MP-10 one at a time;
MP-11, MP-13, MP-14 independent; MP-12 after M75 W-9; MP-15 last. R2: at most 5 build items in progress.

## 11. Owner decisions
Rows M76Q-1..5 in docs/OPEN_QUESTIONS.md.

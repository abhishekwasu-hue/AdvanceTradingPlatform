# M68 TS-1 - audit of hard-coded / pre-chosen trading values

Owner order M68 (31 Oct, `docs/owner-orders/ATP_ORDER_M68.md`). TS-1 is the audit only: no code changes in this PR.

**The rule (binding, M68):** ATP never pre-chooses, caps or silently changes a trading choice. Fixed are only what the
exchange or the broker enforces (lot-size multiple, freeze quantity -> auto-split, market hours / holidays, broker margin
and order-type support, broker rejections) and the kill switch the user presses himself. Exits are never blocked
(ADR-0004). AI never places or changes orders (ADR-0006). Every user choice moves to Settings › Trade settings (TS-2),
starting EMPTY / OFF ("Not set"); templates, Copilot and the engine may only SUGGEST (TS-4).

**Method.** A grep sweep over `backend/app` and `frontend/src` (tests, fixtures and stories left out): `default=`,
`server_default=`, `Field(default=...)`, dataclass / pydantic / parameter defaults, module constants, env defaults in
`app/core/config.py`, alembic `server_default`s, `useState(...)` initial values, object literals that pre-fill a request,
and the words MIS / NRML / MARKET / LIMIT / SL-M, lots, quantity, stop, target, trail, square, cut-off, 15:xx / 09:xx,
vix, reward, cooldown, slippage, brokerage, capital, margin, approval, window, ATM / OTM, width, expiry, hedge, max_.
Read by grep + line ranges. One row per distinct value and home; several homes of the same value share a row.
Line numbers are of `origin/main` at `e2f18883`. Risk checks already moved by M64 (`docs/design/M64_RISK_CHECKS.md`) are
listed only where a value or a cap is still pre-chosen.

**New home** values: `user setting: <Trade settings section.field>` / `fact` (exchange-broker fact, with the reason) /
`delete` / `suggestion-only (TS-4)` / `keep` (operational safety or a record, not a trading choice). A row whose notes
say **ceiling** is a cap on a user's value, classified as a fact or as ATP-imposed.

## Summary

| Section | Rows | user setting | fact | delete | suggestion-only (TS-4) | keep |
|---|---|---|---|---|---|---|
| Order defaults | 16 | 14 | 1 | 1 | 0 | 0 |
| Position size | 9 | 7 | 1 | 1 | 0 | 0 |
| Exits | 9 | 6 | 0 | 3 | 0 | 0 |
| Options | 13 | 10 | 1 | 2 | 0 | 0 |
| Risk limits | 12 | 11 | 0 | 1 | 0 | 0 |
| Automation | 4 | 4 | 0 | 0 | 0 | 0 |
| Paper account | 8 | 7 | 0 | 1 | 0 | 0 |
| Presets / templates / suggestions | 12 | 0 | 0 | 0 | 12 | 0 |
| Exchange / broker facts kept | 12 | 0 | 12 | 0 | 0 | 0 |
| Other | 6 | 1 | 0 | 2 | 0 | 3 |
| **Total** | **101** | **60** | **15** | **11** | **12** | **3** |

Who is hit hardest today: every automated entry without a size trades **one lot** (TS1-017), every INTRADAY bot stops
new entries at **15:00** and squares off at **15:15** (TS1-026), a new paper account starts with **₹10,00,000** and pays
**₹20 per order + 0.02 % slippage** (TS1-064, TS1-069), and the platform ceilings still clamp a user's risk per trade at
**2 %** (TS1-048). Ceilings found: 11 rows (TS1-034, 046, 047, 048, 049, 050, 068 ATP-imposed; TS1-084, 087, 088, 093
facts).

Already clean (no row): the organisation's risk limits (`RiskConfig`, DEF-0, every limit `None`), the Risk limits switches
(M64, every switch OFF), `trading_defaults` / `my_limits` / `strategy_exits` prefs (every field `None`), the chain
ticket's product (none until picked, F1), Build a strategy (`strategyFlow/model.ts` `EMPTY_OPTIONS` / `EMPTY_EXITS`),
the Copilot strategy builder (adds no stop, target or size), DSL strategies (`CustomStrategyConfig`: no stop / target /
minimum R:R unless set), the seller packs (REV-H3: no side or structure chosen), a bot's premium stop, credit levels and
underlying-breach exit (all `None`), and `template_exits` (omitted = the template's own choice, TS1-074).

## 1. Order defaults

| # | file:line | current value | who it affects | new home | notes |
|---|---|---|---|---|---|
| TS1-001 | backend/app/paper/fno_ticket.py:80-81 | `OrderAsk.order_type = "MARKET"`, `product = "MIS"` | all users: chain ticket, basket legs, position actions when the caller omits them | user setting: Order defaults.order_type / .product | the web ticket sends no product until one is picked (F1); the server fallback goes: a missing field -> the ticket asks at the field (TS-3) |
| TS1-002 | backend/app/paper/fno_routes.py:111-112 | `OrderBody.order_type = "MARKET"`, `product = "MIS"` | all users of the F&O order API | user setting: Order defaults.order_type / .product | same as TS1-001 at the API edge: a missing value is a field error the ticket shows next to the field |
| TS1-003 | frontend/src/fno/ticketModel.ts:60 | `newDraft` order type `"MARKET"` | new users (no saved ticket defaults) | user setting: Order defaults.order_type | "Save as my default" exists (`trading_defaults.ticket`, backend/app/me/prefs.py:266-270); the lots on the same line are TS1-020 |
| TS1-004 | backend/app/me/prefs.py:209 | `SavedBasket.product = "MIS"` | saved baskets stored without a product | user setting: Order defaults.product | pref schema default; a basket without a product loads with the product empty |
| TS1-005 | backend/app/execution/products.py:16-18 | holding `None` -> INTRADAY -> MIS | all automated entries without a holding | user setting: Order defaults.holding (intraday / carry forward) | the MIS / CNC / NRML codes are broker facts (TS1-091); only the INTRADAY fallback is a pre-choice |
| TS1-006 | backend/app/deployments/routes.py:233 | `DeploymentCreateRequest.holding = "INTRADAY"` | new bots via the API or a form that omits it | user setting: Order defaults.holding | the form asks (TS-3); today the strategy's candles decide it in the forms (SR-C2) |
| TS1-007 | backend/app/db/models.py:674 | `strategy_deployments.holding` NOT NULL `server_default "INTRADAY"` | all bots | user setting: Order defaults.holding | migration: column NOT NULL default INTRADAY (alembic e4a6c8d0f2b3); existing bot rows keep their value |
| TS1-008 | backend/app/execution/signal_execution.py:87; execution/approval.py:213, 426, 437, 544, 596; execution/multileg.py:104, 414 | `holding: str = "INTRADAY"` parameters and `evidence.get("holding") or "INTRADAY"` | webhook, public API, Telegram, scan and approval paths | user setting: Order defaults.holding | an automated signal with no holding reads the user's Trade settings; Not set -> no order and a notice with a link to the field |
| TS1-009 | backend/app/execution/router.py:124; execution/prechecks.py:122; execution/contract_execution.py:153; execution/multileg.py:583, 606, 623, 650, 708 | `product: str = "MIS"` parameter defaults; the written-option margin probe always asks the MIS margin | LIVE margin checks, multi-leg legs | delete | internal defaults (callers pass `product_for(...)`); the probe should ask the order's own product (NRML margin differs) |
| TS1-010 | backend/app/deployments/routes.py:236; backend/app/db/models.py:678 | `order_style = "MARKET"` (API default and NOT NULL `server_default "MARKET"`) | LIVE bot entries | user setting: Order defaults.order_type | migration: column NOT NULL default MARKET (alembic d5e7f9a1c3e6); PAPER fills unaffected today |
| TS1-011 | backend/app/execution/router.py:74; execution/marketable_limit.py:30 | `DEFAULT_MARKET_PROTECTION_PCT` / `DEFAULT_PROTECTION_PCT` 0.5 % | LIVE PROTECTED_LIMIT bots without their own % | user setting: Order defaults.price_protection_pct | one field for TS1-011 to TS1-013; Not set with PROTECTED_LIMIT -> the form asks |
| TS1-012 | frontend/src/pages/DeploymentsPage.tsx:107-109 | `orderStyle "MARKET"`, `protectionPct "0.5"` pre-filled | full bot form | user setting: Order defaults.order_type / .price_protection_pct | pre-fill only from the user's own Trade settings; open PR claude/m63-i7-one-formatter-3 touches this file (formatting only, no overlap) |
| TS1-013 | backend/app/me/prefs.py:351; frontend/src/settings/smartExecutionSettings.tsx:22 | `smart_execution.protection_band_pct` 0.5 % | brokers without market orders (marketable limit) | user setting: Order defaults.price_protection_pct | a pref with a starting value today; Not set -> such a broker asks for the band or a limit price |
| TS1-014 | backend/app/brokers/base.py:106; backend/app/core/config.py:639 | protective stop-limit 1 % past the trigger (operator env `STOP_LIMIT_BAND_PCT` overrides) | LIVE stops sent as SL (stop-limit) | user setting: Order defaults.stop_limit_band_pct | operator env today; Not set -> asked where the exchange takes no SL-M (TS1-092) |
| TS1-015 | backend/app/paper/fno_ticket.py:88; paper/fno_routes.py:120; backend/app/db/models.py:785, 790 | `trigger_mode "TOUCH"`, `trigger_source "PREMIUM"` | waiting limit / stop orders | user setting: Order defaults.trigger_mode / .trigger_source | ORM default only (every row is written with a value): no migration |
| TS1-016 | backend/app/core/config.py:601, 605 | `ORDER_MARKET_PROTECTION_PCT` unset; `LIVE_MARKET_PROTECTION` off (-1 = the broker's automatic band) | LIVE MARKET / SL-M orders | fact (SEBI retail-algo market protection; a broker rejects an API market order without it) | the optional % becomes Order defaults.market_protection_pct; Not set = the broker's automatic band |

## 2. Position size

| # | file:line | current value | who it affects | new home | notes |
|---|---|---|---|---|---|
| TS1-017 | backend/app/risk_engine/risk_manager.py:123, 129 | no size given -> **one lot** (`typed if typed is not None else float(unit)`) | all users: bots without max lots, webhooks, public API, Telegram, scan entries | user setting: Position size.mode + .lots | the widest pre-choice. TS-3: a bot form asks; an automated signal with no size set -> no order and a notice with a link |
| TS1-018 | backend/app/core/models.py:123; backend/app/db/models.py:1066; backend/app/paper/ticket.py:170 | `RiskConfig.lot_size = 1`; `risk_settings.lot_size` NOT NULL default 1 ("the organisation's unit" for cash) | cash-equity orders | fact (a cash lot is 1 share) | the organisation's unit becomes Position size.quantity (fixed-quantity mode); migration: column NOT NULL default 1 (ORM) |
| TS1-019 | frontend/src/paperbot/model.ts:88-94 | "Start on paper": `max_lots: 1` with ATM, NEAREST, offset 0, BUY; timeframe 1min / day | new paper bots | user setting: Position size.lots (+ Options fields, TS1-037) | the form asks before Start (REV-H3 already asks a seller pack's structure); open PR claude/m63-i7-silent-catches-2 touches PaperBotV2.tsx (comments only) |
| TS1-020 | frontend/src/fno/ticketModel.ts:60, 88-91 | `newDraft` lots 1; `clampLots` -> at least 1 | new users | user setting: Position size.lots | an empty lots field, focused (TS-3); open PR claude/m63-i7-silent-catches-2 touches ticketModel.ts (a comment only) |
| TS1-021 | frontend/src/fno/PositionActionsV2.tsx:36 | exit part / add / reverse sheet starts at 1 lot | all users (position actions) | user setting: Position size.lots | an exit-part size: empty and focused; exits never blocked (ADR-0004) |
| TS1-022 | frontend/src/fno/hubModel.ts:175; frontend/src/fno/ChainActionSheetV2.tsx:78 | a leg added to the basket gets lots 1 | basket builder | user setting: Position size.lots | lots from Trade settings, else empty |
| TS1-023 | backend/app/paper/fno_basket.py:42-43, 160-163 | `NEUTRAL_LOTS 1`, `NEUTRAL_WIDTH_STEPS 2` when the user has no options bot | basket ready-made structures | user setting: Position size.lots / Options.spread_width | read Trade settings; none -> the panel asks (no stop is set here today) |
| TS1-024 | backend/app/db/models.py:2745-2746 | `paper_sub_accounts.sizing_mode "MULTIPLIER"`, `sizing_value 1` (server defaults) | M45 paper fan-out sub-accounts | user setting: Position size.fanout (per sub-account) | migration: NOT NULL server defaults MULTIPLIER / 1 (alembic f1b3d5e7a9c2) |
| TS1-025 | backend/app/core/config.py:901-907; backend/app/risk_engine/by_default.py:127-160 | `RISK_DEFAULT_*` (1 % risk, ₹0, 2 % daily loss, 10 entries, R:R 1.5) kept to read rows saved before TT-12 | nobody new (legacy reading only) | delete | after TS-6 has marked those rows "saved / Not set"; removal of dead code needs the owner's ✅ (CLAUDE.md §8) |

## 3. Exits

| # | file:line | current value | who it affects | new home | notes |
|---|---|---|---|---|---|
| TS1-026 | backend/app/workers/trading_worker.py:103-104; backend/app/market_data/calendar.py:49-55 | intraday: no new entries after **15:00**, square-off at **15:15** (NSE; MCX 23:00 / 23:15; CDS 16:30 / 16:45) | all INTRADAY bots and intraday paper positions | user setting: Exits.square_off_time + Automation.trading_window | owner decision D1. Open / close of the venue stay facts (TS1-084); the broker's own MIS auto square-off is a broker fact the ticket can show |
| TS1-027 | backend/app/backtest/engine.py:98; backend/app/backtest/options_engine.py:80-82 | backtests copy 15:00 / 15:15 (and settlement from 15:15) | backtests | user setting: Exits.square_off_time (the live value) | parity (scan = backtest = live): changes together with TS1-026 |
| TS1-028 | backend/app/screener/then/auto_paper.py:192-202; backend/app/core/config.py:828-830 | scan Auto PAPER last entry = the venue cut-off or `SCAN_AUTO_PAPER_LAST_ENTRY` | scan Auto PAPER (flag off) | user setting: Automation.trading_window | follows TS1-026 |
| TS1-029 | frontend/src/fno/BasketPanelV2.tsx:199; frontend/src/fno/CompareV2.tsx:347 | when the basket settings fail to load: width 2, lots 1, **sold-leg stop 50 %** | basket ready-made / compare | delete | a hidden stop nobody set (the server's own start has no stop, TS1-023); say "settings not loaded" and leave the fields empty |
| TS1-030 | backend/app/me/prefs.py:355-356; backend/app/execution/smart_execution.py:49-50; frontend/src/settings/smartExecutionSettings.tsx:22 | incomplete multi-leg order: close the filled legs after 5 minutes without an answer | users of multi-leg orders | user setting: Exits.incomplete_legs_action / .answer_minutes | Not set = ATP does nothing by itself: the legs stay and the notice stays open |
| TS1-031 | backend/app/execution/group_exits.py:65-70; backend/app/db/models.py:844, 846; frontend/src/fno/strategyExits.ts:72 | combined exit: check TOUCH, scope SELECTED; measure SPOT for a spread, else MTM ₹ | combined / strategy exits | user setting: Exits.combined.measure / .check_mode | trail NONE and no SL are already off; ORM defaults only: no migration |
| TS1-032 | backend/app/core/config.py:611 | `LIVE_EXIT_IF_NO_STOP` off (close a LIVE position whose broker stop was rejected) | LIVE positions | user setting: Exits.close_if_broker_stop_rejected | operator env today; Not set = OFF, as now |
| TS1-033 | backend/app/core/config.py:928 | `TICKET_TIME_STOP_MIN_LEAD_MINUTES` 5 | paper ticket time stop | delete | keep only "must be later than now"; the 5-minute lead is ATP's |
| TS1-034 | backend/app/deployments/routes.py:101-104; backend/app/me/prefs.py:260-263 | trailing ≤ 50 %, break-even ≤ 10 R, time exit ≤ 375 minutes | bot exits | delete | ceiling, ATP-imposed (50 % / 10 R); 375 = one NSE session, a fact for intraday only, wrong for a swing bot |

## 4. Options

| # | file:line | current value | who it affects | new home | notes |
|---|---|---|---|---|---|
| TS1-035 | backend/app/deployments/routes.py:166, 171-178, 182, 194-196; backend/app/instruments/contracts.py:38-40 | missing expiry -> NEAREST, strike -> ATM, ITM / OTM offset -> 1, side inferred (WRITE for a structure, else BUY) | OPTION bots created without these fields | user setting: Options.expiry_rule / .strike_rule / .strike_offset / .option_position | TS-3: a field error the form shows at the field, no silent fill |
| TS1-036 | backend/app/deployments/routes.py:124; backend/app/db/models.py:648 | `spread_width = 2` (API default and NOT NULL default 2) | multi-leg bots | user setting: Options.spread_width | migration: column NOT NULL server_default 2 (alembic e5a1c9d7f064) |
| TS1-037 | frontend/src/pages/DeploymentsPage.tsx:113-117, 124 | kind OPTION, BUY, NEAREST, ATM, offset 1, width 2 pre-selected | full bot form | user setting: Options.* (pre-fill only from Trade settings) | open PR claude/m63-i7-one-formatter-3 touches the file (formatting only) |
| TS1-038 | frontend/src/pages/DeploymentsPage.tsx:126-132 | CUSTOM legs start as ATM short PE + OTM-2 long PE; "Add leg" = OTM-1 short CE | CUSTOM structure form | user setting: Options.custom_legs | start empty; the user adds each leg |
| TS1-039 | frontend/src/pages/BacktestPage.tsx:113-116, 123-124 | backtest form: NEAREST, ATM, offset 0, width 2, the same CUSTOM legs | backtests | user setting: Options.* (from Trade settings, else asked) | open PRs claude/m63-i7-one-formatter-2 / -3 touch the file (formatting only) |
| TS1-040 | frontend/src/setup/model.ts:305-311 | `paperDeployment()`: option BUY, NEAREST, ATM | none today (helper kept, Setup stopped using it in REV-1 M2) | delete | the pre-chosen contract goes; the file stays (CLAUDE.md §8) |
| TS1-041 | backend/app/instruments/spreads.py:95 | `HOLD_FOR_DAYS` = winged credit structures only (no SWING straddle / strangle / written single) | SWING option bots | user setting: Risk limits.overnight_short_options (switch, OFF) | an ATP rule; the interview says the same (backend/app/ai/interview.py:505-508) |
| TS1-042 | backend/app/instruments/spreads.py:100, 330, 707 | a ratio spread sells 2 per bought leg; condor / strangle at least OTM-1; straddle / butterfly ATM | structure definitions | user setting: Options.ratio | ATM straddle / OTM strangle are the structures' definitions (kept); the 1:2 ratio is a choice (CUSTOM legs already allow 1-4) |
| TS1-043 | backend/app/instruments/strike_selection.py:46, 49; backend/app/deployments/routes.py:90, 93 | delta tolerance 0.10; search 5 strikes either side | bots that use a delta or chain filter | user setting: Options.strike_filters.delta_tolerance / .search_steps | used only once the user set a target delta or a filter; shown with the filter |
| TS1-044 | backend/app/me/prefs.py:473 | `option_chain.expiry_day_next = True` (an expiry day opens the next expiry) | chain, Terminal basket | user setting: Options.expiry_day_roll | Not set = the nearest expiry; the other stays selectable |
| TS1-045 | backend/app/me/prefs.py:347-350; frontend/src/settings/smartExecutionSettings.tsx:22 | physical-settlement warning: 4 days before expiry, within 2 % of the strike, Telegram on | stock F&O holders | user setting: Options.settlement_warning | information only; physical settlement itself is an exchange fact |
| TS1-046 | backend/app/paper/fno_ticket.py:64; frontend/src/fno/ticketModel.ts:51; backend/app/me/prefs.py:195, 268, 286; backend/app/deployments/routes.py:120 | `MAX_LOTS` 500 per ticket / leg / bot | all users | delete | ceiling, ATP-imposed: the comment says the freeze quantity was not in the master yet; the freeze-quantity auto-split now exists (TS1-087) |
| TS1-047 | backend/app/deployments/routes.py:118, 124, 205; backend/app/instruments/spreads.py:101-102; backend/app/paper/fno_basket.py:41 | strike offset ≤ 10 (custom leg ≤ 20), width ≤ 20, ≤ 6 custom legs, ratio ≤ 4, ≤ 12 basket legs | option bots, baskets | fact (listed strikes; the broker's basket limit) | ceiling: replace the ATP numbers with "a strike that is listed" and the broker's own basket size |

## 5. Risk limits

| # | file:line | current value | who it affects | new home | notes |
|---|---|---|---|---|---|
| TS1-048 | backend/app/platform/controls.py:713-718 | platform ceilings: risk per trade ≤ **2 %**, daily loss ≤ 5 %, portfolio risk ≤ 10 %, drawdown pause ≤ 25 % | all organisations: a user's higher value is clamped (by_default, ticket, basket, guardian, generator) | user setting: Risk limits.ceilings (admin, none by default) | owner decision D2. DECISIONS 30 Oct: "org / admin ceilings OFF by default" - these still clamp. Open PRs claude/m66-en1-engine-boundary / en2-data-adapter touch controls.py (flag lines only, no overlap) |
| TS1-049 | backend/app/risk_engine/by_default.py:58-67; frontend/src/setup/model.ts:195-197 | paper ticket bounds: risk 0.05-5 %, daily loss 0.1-20 %, entries 0-500, R:R 0-10, bucket risk ≤ 50 % | paper ticket limits | user setting: Risk limits.* (one store) | ceiling, ATP-imposed (5 % / 20 %); one store instead of two (organisation row + ticket JSON) |
| TS1-050 | backend/app/risk_engine/risk_limits.py:58-87 | input bounds per check (lots ≤ 100,000, positions ≤ 200, entries ≤ 500, cool-down ≤ 1,440 min, R:R 0.1-10 ...) | Settings › Risk limits | user setting: Risk limits.* (finite, positive) | ceiling, ATP-imposed but wide; keep only finite / positive checks |
| TS1-051 | backend/app/core/models.py:132; backend/app/db/models.py:1069 | `loss_streak_pause = "next_session"` (NOT NULL server_default) | users with the losses-in-a-row check on | user setting: Risk limits.loss_cooldown.pause_mode | asked when the check is switched on; migration: column NOT NULL default next_session (alembic b5d7f9a1c3e6) |
| TS1-052 | backend/app/core/models.py:149; backend/app/db/models.py:1082; backend/app/workers/trading_worker.py:2456-2460 | `opposite_positions = "deny"` (NOT NULL server_default) | all automated entries: a bot skips an entry opposite another bot's open position | user setting: Risk limits.opposite_positions | owner decision D3; migration: column NOT NULL default deny (alembic c7e9a1b3d5f7) |
| TS1-053 | backend/app/core/models.py:122; backend/app/db/models.py:1059 | `min_risk_reward = 0.0` = not set (NOT NULL) | - | user setting: Risk limits.min_reward_risk | already Not set (0 sentinel); optional migration to NULL, no behaviour change |
| TS1-054 | backend/app/risk_engine/guardian.py:56 | `DRAWDOWN_CUT_MULTIPLIER 0.5`: the first drawdown step halves the size | users with the drawdown check on | user setting: Risk limits.drawdown.cut_pct | the check is OFF (M64); the 50 % is ATP's |
| TS1-055 | backend/app/execution/contract_execution.py:36; execution/prechecks.py:38, 122; execution/multileg.py:583 | `MARGIN_SAFETY 0.8` as the callers' default share of margin | LIVE margin caps when no config is passed | delete | M64 made it the user's switch (`RiskConfig.margin_share`); the fallback becomes the whole available margin (a broker fact) |
| TS1-056 | backend/app/risk_engine/vix_gate.py:1-30; backend/app/core/config.py:916-929 | India VIX gate: change ≥ 5 % pauses long entries on NIFTY / BANKNIFTY / SENSEX; level ≥ 20 cuts the size 50 %; checked from 09:20; no reading after the grace = a spike | every user of an organisation with flag `vix_gate` on (the trader pack turns it on: backend/app/platform/trader_pack.py:57-58) | user setting: Risk limits.vix_gate (own switch + values, OFF) | an admin flag + operator env today, no user switch; open PRs m66-en1 / en2 touch trader_pack.py (another line only) |
| TS1-057 | backend/app/core/config.py:857-859; backend/app/paper/experience.py:51-55 | experience gates: Advanced at 100 trades / 60 sessions; opt-out reason ≥ 15 characters | users who switch "Limit me by my experience level" on (OFF) | user setting: Risk limits.experience (his own thresholds) | the switch is already OFF; the numbers are ATP's |
| TS1-058 | backend/app/db/models.py:899; backend/app/risk_engine/guardian.py:57 | a new market event row defaults to SIZE_CUT | admins listing event days | user setting: Risk limits.event_rules | the action is asked when the event is added; the check itself is OFF (M64) |
| TS1-059 | backend/app/risk_engine/hierarchy.py:35 | an admin limit warns at 80 % of the limit | team admin limits (none by default) | user setting: Risk limits.ceilings.warn_at (admin) | a notice only, never blocks |

## 6. Automation

| # | file:line | current value | who it affects | new home | notes |
|---|---|---|---|---|---|
| TS1-060 | backend/app/deployments/routes.py:240; backend/app/db/models.py:683 | `entry_mode = "AUTO"` (API default and NOT NULL server_default) | new bots | user setting: Automation.approval_mode | migration: column NOT NULL default AUTO (alembic f3a9c1e7b2d4); the form asks |
| TS1-061 | backend/app/execution/approval.py:56-66; backend/app/platform/trader_pack.py:57 | organisation flag `approval_only_entries`: every bot of the organisation approval-only (the trader pack turns it on) | all users of that organisation | user setting: Automation.approval_mode (per user) | the flag stays an admin control for teams, OFF by default |
| TS1-062 | backend/app/core/config.py:806-810 | an entry proposal lives 15 min, an exit proposal 30 min; re-quote drift 0.5 %; at most 2 re-quotes | approval-mode users | user setting: Automation.approval.ttl_minutes / .requote_drift_pct / .max_requotes | operator env today |
| TS1-063 | backend/app/core/config.py:825-830 | scan Auto PAPER: at most 3 a day, 30-minute cool-down per symbol, an arming lasts 7 days | scan Auto PAPER (flag off, never in the trader pack) | user setting: Automation.scan_auto.* | operator env today; the trading window is TS1-028 |

## 7. Paper account

| # | file:line | current value | who it affects | new home | notes |
|---|---|---|---|---|---|
| TS1-064 | backend/app/core/config.py:837; backend/app/paper/service.py:60 | a new paper account starts with **₹10,00,000** | new users | user setting: Paper account.capital | owner decision D4; asked at the first paper order (TS-3); migration: `paper_accounts.starting_capital` NOT NULL (written from config) -> nullable + a saved marker |
| TS1-065 | backend/app/risk_engine/by_default.py:421; backend/app/terminal/status.py:129; backend/app/paper/experience.py:277; backend/app/paper/fno_insights.py:83; backend/app/ai/monitor.py:85 | no account row -> ₹10,00,000 as the capital basis | users without a paper account | user setting: Paper account.capital | Not set -> a % shows "needs your capital" instead of a basis nobody chose |
| TS1-066 | backend/app/backtest/engine.py:75; backend/app/backtest/options_engine.py:272; backend/app/backtest/routes.py:366; backend/app/ai/draft_backtest.py:98; backend/app/ai/research_loop.py:137, 156 | backtests size on ₹10,00,000 when no capital is set | backtests, Copilot drafts | user setting: Paper account.capital | the same value as live paper (parity) |
| TS1-067 | frontend/src/setup/model.ts:192; frontend/src/setup/StepsV2.tsx:127, 184 | the Setup step starts the capital at ₹1,00,000 | new users in Setup | user setting: Paper account.capital | mismatch with the server's ₹10,00,000 (TS1-064) |
| TS1-068 | backend/app/core/config.py:838-839 | paper capital ₹10,000 - ₹10 crore | all users | delete | ceiling, ATP-imposed; keep "above zero" |
| TS1-069 | backend/app/execution/paper_broker.py:22, 26; backend/app/risk_engine/by_default.py:357-358 | brokerage **₹20 per order**, slippage **0.02 % per side** | every paper fill and cost line, backtests | user setting: Paper account.charges_model (brokerage plan + slippage) | owner decision D4 |
| TS1-070 | backend/app/execution/india_costs.py:292, 309, 320 | `brokerage_per_order = 20.0` parameter defaults | cost estimates (ticket, Options Builder, structures) | user setting: Paper account.charges_model.brokerage | callers pass the user's value |
| TS1-071 | backend/app/options_builder/routes.py:91; frontend/src/pages/OptionsBuilderPage.tsx:39 | Options Builder cost line brokerage ₹20 | Options Builder | user setting: Paper account.charges_model.brokerage | the field already says "the trader's own"; start from his setting, else empty |

## 8. Presets / templates / suggestions (TS-4)

| # | file:line | current value | who it affects | new home | notes |
|---|---|---|---|---|---|
| TS1-072 | backend/app/strategy_engine/base.py:70; indicator_strategies.py:30-31, 110-111, 191-192; combo_strategies.py:81, 121, 161, 204; mtf_strategy.py:37, 39; swing_strategies.py:49, 96 | built-in strategies: `target_rr` (1.2 / 2.0 up to 2.0 / 3.5) and ATR stop multiples (0.3-1.2) | users of built-in strategies | suggestion-only (TS-4) | STRAT-X (30 Oct): shown as "suggested" settings; nothing applied until the user saves |
| TS1-073 | backend/app/strategy_engine/combo_strategies.py:203 | Opening Range Breakout: range 15 min, last entry 14:30 | ORB users | suggestion-only (TS-4) | a time setting inside a generic template (allowed as a user setting, CLAUDE.md §5) |
| TS1-074 | backend/app/strategy_templates/exits.py:7-11, 51; backend/app/strategy_templates/templates/*.json | a template bot's stop = the template's invalidation; `stop_mult` 1.0 (16 templates) / 0.5 (C6), `hold_bars` 30, `expiry_bars` 20 | template bots | suggestion-only (TS-4) | white-box template numbers, shown as suggestions the user accepts or edits |
| TS1-075 | backend/app/ai/strategist.py:46-49, 444-445; backend/app/ai/routes.py:1199 | Strategy Lab draft: at most 3 trades a day, last entry 14:45, square-off 15:15, costs 0.03 %; the draft bot: INTRADAY, time exit 15:10, break-even at 1 R | Copilot Strategy Lab | suggestion-only (TS-4) | the draft carries these exits into the create call today; each value shown as a suggestion |
| TS1-076 | backend/app/ai/interview.py:447, 459 | interview risk answer -> 0.5 / 1 / 1.5 % risk per trade | strategy interview | suggestion-only (TS-4) | - |
| TS1-077 | backend/app/ai/interview.py:502, 518, 526, 537-538 | interview contract plan: futures NEAREST; a bought option ITM-1 (MONTHLY for swing, NEXT on an index, else NEAREST); selling -> bull put / bear call / iron condor, width 2, take profit at 50 % of the credit, stop at 150 % | strategy interview | suggestion-only (TS-4) | - |
| TS1-078 | backend/app/ai/prompt.py:35, 277-281, 292 | Copilot generator rules: stop ≥ 1 ATR ("the platform raises a tighter one"), minimum R:R ≥ 1 | Copilot strategy generator | suggestion-only (TS-4) | "raises a tighter one" changes the user's stop: becomes a suggestion |
| TS1-079 | backend/app/ai/monitor.py:39-41 | Copilot nudges at 3 losses in a row, 2 % day drawdown, 3 errors | Copilot monitor (information) | suggestion-only (TS-4) | use the user's own limits when set; none -> no nudge |
| TS1-080 | backend/app/paper/manual.py:17 | Telegram `/paper`: warns at reward:risk above 3 | Telegram manual paper | suggestion-only (TS-4) | - |
| TS1-081 | backend/app/options_builder/templates.py:48; backend/app/options_builder/routes.py:116, 152-153 | ready-made structures: 1 lot at ATM ± width; lots 1; selector PoP ≥ 70 %, 2 strikes OTM | Options Builder (research) | suggestion-only (TS-4) | - |
| TS1-082 | backend/app/price_action/level_gate/settings.py:14-27, 42-43; backend/app/price_action/zones/settings.py:89 | engine presets (a minimum reward:risk, an entry window, a lot multiplier, a stop buffer, a retry wait, a session end) | price-action engine signals (M66) | suggestion-only (TS-4) | values stay in the file; the engine may only suggest. Open PRs claude/m66-en1 / en2 add the engine boundary, they do not touch these files |
| TS1-083 | backend/app/core/config.py:840-852 | progression ladder card: 20 sessions, 30 trades, drawdown ≤ 10 %, discipline 70 | paper users (information card) | suggestion-only (TS-4) | information; no switch in Phase 0 |

## 9. Exchange / broker facts kept

| # | file:line | current value | who it affects | new home | notes |
|---|---|---|---|---|---|
| TS1-084 | backend/app/market_data/calendar.py:26-27, 49-55; `market_holidays` table | market open / close per venue (NSE 09:15-15:30, MCX, CDS) and holidays | all | fact (market hours / holidays) | the no-new-entries / square-off columns of the same table are TS1-026; ceiling (an order outside the session) = fact |
| TS1-085 | backend/app/execution/resting_book.py:38 | a waiting entry rests until 15:30 | waiting orders | fact (DAY order validity) | - |
| TS1-086 | backend/app/trading/trigger_rules.py:28 | N-minute candles counted from the NSE open | CLOSE-mode triggers | fact (session start) | - |
| TS1-087 | backend/app/instruments/freeze_limits.py:29-30; backend/app/execution/smart_execution.py:81-91 | freeze quantity table -> auto-split | all F&O orders | fact (freeze quantity) | ceiling = fact; splits, never refuses (M64 #25) |
| TS1-088 | backend/app/db/models.py:2930; backend/app/instruments/master.py:154-156 | lot size and tick from the instrument master; lot 1 when a master row has none | all F&O orders | fact (lot multiple) | ceiling = fact; the fallback 1 for a missing master value should refuse instead of assuming (check in the Position size PR) |
| TS1-089 | backend/app/execution/marketable_limit.py:31 | tick 0.05 when the instrument's tick is unknown | marketable limits | fact (tick size) | prefer the master's tick |
| TS1-090 | backend/app/execution/india_costs.py:43-74 | dated statutory charges (STT, exchange, stamp, GST, SEBI fee) | cost model | fact (statutory rates) | the `PaperBroker` constructor rates (paper_broker.py:23-25) are the same kind |
| TS1-091 | backend/app/execution/products.py:10-22 | MIS / CNC / NRML product codes | all orders | fact (broker product codes) | only the INTRADAY fallback is a pre-choice (TS1-005) |
| TS1-092 | backend/app/core/config.py:608 | option stops as SL (stop-limit) where SL-M is not offered | LIVE option stops | fact (exchange order-type support) | the band is TS1-014 |
| TS1-093 | backend/app/execution/ops_throttle.py:1-15; backend/app/core/config.py:243-244 | orders per second from the IN-SEBI rule-set | LIVE | fact (SEBI orders-per-second) | ceiling = fact; exits first (ADR-0004) |
| TS1-094 | backend/app/execution/marketable_limit.py:65; backend/app/execution/router.py:243 | a broker without API market orders gets a marketable limit | those brokers | fact (broker order-type support) | the band is TS1-013 |
| TS1-095 | backend/app/execution/signal_execution.py:42-80, 183; backend/app/execution/prechecks.py:64, 120-160 | kill switch, F&O ban, market open, margin available, broker rejections (M64 section C) | all | fact | the kill switch is the user's own button (kept) |

## 10. Other

| # | file:line | current value | who it affects | new home | notes |
|---|---|---|---|---|---|
| TS1-096 | backend/app/execution/router.py:124, 314 | a LIVE entry with a stop also places that stop at the broker | LIVE entries | keep | executes the user's own stop (ADR-0004); not a pre-chosen value |
| TS1-097 | backend/app/core/config.py:389-390, 622, 864 | exit retries 0; stop re-arm stops after 3 rejects; a PAPER "Exit now" needs a price at most 5 minutes old | exits | keep | operational safety, not a trading choice; the 5-minute rule refuses a PAPER Exit now without a fresh price - check it against ADR-0004 in the Exits PR |
| TS1-098 | frontend/src/pages/DeploymentsPage.tsx:96, 98; frontend/src/paperbot/PaperBotV2.tsx:424; frontend/src/copilot/tabs/StrategyLabTab.tsx:77-78; frontend/src/components/ChartStrategies.tsx:131 | NIFTY 50 / timeframe 1min / style intraday pre-selected | bot forms, Strategy Lab, chart strategies | delete | what to trade is the user's pick; ChartStrategies.tsx is in open PRs formatter-2 / -3 (formatting only) |
| TS1-099 | backend/app/deployments/routes.py:219-220; backend/app/db/models.py:610-611 | exchange NSE, timeframe 1min | new bots via the API | delete | strategy fields (not Trade settings): asked |
| TS1-100 | backend/app/core/config.py:429 | risk-free rate 7 % for Greeks and models | option analytics | user setting: Options.risk_free_rate (Advanced) | a model input, not an order value |
| TS1-101 | backend/app/db/models.py:398 | `trades.holding` NOT NULL server_default INTRADAY | trade records | keep | the record of what happened; no change |

## Needs a migration

DB columns whose server / ORM default pre-sets a trading value (timing per DECISIONS 25 Oct). Existing users keep what
they saved (TS-6); a bot row keeps its own value (it was created through a form); only the default for new rows goes.

1. `strategy_deployments.holding` NOT NULL server_default `INTRADAY` (TS1-007)
2. `strategy_deployments.order_style` NOT NULL server_default `MARKET` (TS1-010)
3. `strategy_deployments.entry_mode` NOT NULL server_default `AUTO` (TS1-060)
4. `strategy_deployments.spread_width` NOT NULL server_default `2` (TS1-036)
5. `risk_settings.loss_streak_pause` NOT NULL server_default `next_session` (TS1-051)
6. `risk_settings.opposite_positions` NOT NULL server_default `deny` (TS1-052)
7. `risk_settings.lot_size` NOT NULL default 1 (TS1-018)
8. `paper_sub_accounts.sizing_mode` / `sizing_value` NOT NULL server_default `MULTIPLIER` / `1` (TS1-024)
9. `paper_accounts.starting_capital` NOT NULL, written from config -> nullable + a saved marker (TS1-064)
10. optional: `risk_settings.min_risk_reward` 0 sentinel -> NULL (TS1-053, no behaviour change)

No migration: the ORM-only defaults of `paper_resting_orders` and `group_exits` (TS1-015, TS1-031), the meaning
defaults `instrument_kind UNDERLYING` / `option_strategy SINGLE`, `trades.holding` (a record), and the TS-6 saved markers
of prefs namespaces (JSON).

## Decisions for the owner

Each with a provisional default consistent with M68 (Not set / OFF); work continues on the default.

- **D1 Intraday cut-off and square-off (TS1-026 to 028).** Default: Not set - ATP adds no 15:00 cut-off and no 15:15
  square-off; the broker's own MIS square-off applies (a broker fact, shown on the ticket); a user who sets a time gets
  it for every bot, per bot or per order.
- **D2 Platform risk ceilings (TS1-048).** Default: none (OFF) for every organisation, as DECISIONS 30 Oct; an admin may
  set ceilings for a team.
- **D3 Opposite positions of two bots (TS1-052).** Default: Not set = allowed; the owner's own "deny" is saved in his own
  account as his setting (TS-4: the owner's settings live only in the owner's account).
- **D4 Paper capital and charges (TS1-064 to 071).** Default: Not set - the capital is asked at the first paper order
  (with "Save as my default"); charges Not set = statutory charges only, no brokerage, no slippage, labelled so.
- **D5 Scope of Trade settings.** Today risk settings are one row per organisation and order choices live per bot.
  Default: per user, with per-strategy / per-bot / per-order overrides; a single-member organisation's row is read as
  that user's own (TS-6); admin ceilings stay per organisation.

## Fix PR plan

One PR per Trade settings section (TS-2 order), each behind `ui_v2` for new screens, with tests, the TS-1 rows it
closes marked before / after, and its own migration where listed.

| PR | Scope | TS1 rows | Migration |
|---|---|---|---|
| 0 | Settings › Trade settings shell: one settings registry (extends `trading_defaults` / `my_limits`), every field Not set, saved markers, "Save as my default" plumbing; no behaviour change | - | no |
| 1 | Order defaults | TS1-001 to 016 | yes (2: holding, order_style) |
| 2 | Position size | TS1-017 to 024, TS1-088 (fallback check) | yes (lot_size, sub-account sizing) |
| 3 | Exits (with D1) | TS1-026 to 034, TS1-097 (Exit now check) | no |
| 4 | Options | TS1-035 to 040, 042 to 047, TS1-100 | yes (spread_width) |
| 5 | Risk limits (with D2, D3) | TS1-041, TS1-048 to 059 | yes (loss_streak_pause, opposite_positions, optional min R:R) |
| 6 | Automation | TS1-060 to 063 | yes (entry_mode) |
| 7 | Paper account (with D4) | TS1-064 to 071 | yes (starting_capital) |
| 8 | Suggestions (TS-4): templates, strategy library, Copilot, engine | TS1-072 to 083, TS1-098, TS1-099 | no |
| 9 | TS-6 one-time notice + legacy clean-up, TS-7 CI guard (allow-list = TS1-084 to 096 with their reasons) | TS1-025, TS1-084 to 096 | no |

Rows kept as they are (fact / keep): TS1-016, TS1-084 to 097, TS1-101; the facts form the TS-7 allow-list.
